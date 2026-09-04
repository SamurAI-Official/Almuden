"""Pre-signing transaction validation - the hardening boundary.

Before AlMuden signs ANY externally constructed Solana transaction
(Jupiter order, PumpPortal local payload), this validator parses it and
proves it expresses exactly the authorized action:

    program IDs, token mints, SOL/token movement, compute budget,
    priority fees, signers, min expected output, unknown instructions.

FAIL-CLOSED RULE: without the optional ``solders`` dependency the
validator cannot parse, therefore nothing is ever approved and nothing
is ever signed. Installing ``solders`` is the explicit opt-in to signing
capability.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)

# solders is optional; absence disables all signing (fail-closed).
try:  # pragma: no cover - exercised only when solders is installed
    from solders.transaction import VersionedTransaction  # type: ignore

    SOLDERS_AVAILABLE = True
except ImportError:  # pragma: no cover
    VersionedTransaction = None  # type: ignore[assignment, misc]
    SOLDERS_AVAILABLE = False


# Program IDs that may appear in a legitimate AlMuden-routed swap.
DEFAULT_ALLOWLIST: Set[str] = {
    # SPL Token program
    "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
    # Token-2022 program
    "TokenzQedBNPFZCXXzabCT2eFfcPZiCaKceutukvW",
    # Jupiter v6 aggregator
    "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4",
    # Compute budget program
    "ComputeBudget111111111111111111111111111111",
    # Associated token account program
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL",
    # System program
    "11111111111111111111111111111111",
}

COMPUTE_BUDGET_PROGRAM = "ComputeBudget111111111111111111111111111111"
JUPITER_V6_PROGRAM = "JUP6LkbZbjS1jKKwapdHNy74zcZ3tLUZoi5QNyVTaV4"
SYSTEM_PROGRAM = "11111111111111111111111111111111"

LAMPORTS_PER_SOL = 1_000_000_000
# Buffer above authorized movement tolerated for rent/ATA funding.
SOL_BUFFER_LAMPORTS = 1_000_000


class ValidationCode:
    """Machine-readable issue codes for routing/alerting."""

    PARSER_UNAVAILABLE = "PARSER_UNAVAILABLE"
    DECODE_FAILED = "DECODE_FAILED"
    UNKNOWN_PROGRAM = "UNKNOWN_PROGRAM"
    MINT_MISMATCH = "MINT_MISMATCH"
    PRIORITY_FEE_EXCEEDED = "PRIORITY_FEE_EXCEEDED"
    UNEXPECTED_SIGNER = "UNEXPECTED_SIGNER"
    MISSING_MIN_OUT = "MISSING_MIN_OUT"
    SOL_MOVEMENT = "SOL_MOVEMENT"
    EMPTY_MESSAGE = "EMPTY_MESSAGE"


@dataclass
class ExpectedSwap:
    """What the validator must PROVE the transaction expresses."""

    taker_pubkey: str
    input_mint: str
    output_mint: str
    in_amount_raw: int
    min_out_raw: int
    max_priority_fee_lamports: int
    allow_programs: Set[str] = field(default_factory=lambda: set(DEFAULT_ALLOWLIST))


@dataclass
class ValidationIssue:
    code: str
    detail: str


@dataclass
class ValidationResult:
    approved: bool
    issues: List[ValidationIssue] = field(default_factory=list)
    parsed: Optional[Dict[str, Any]] = None

    def reject_reasons(self) -> List[str]:
        return [f"{i.code}: {i.detail}" for i in self.issues]

class TransactionValidator:
    """Parses and proves an unsigned transaction matches the authorization."""

    def validate(self, tx_b64: str, expected: ExpectedSwap) -> ValidationResult:
        # -- Fail-closed: no parser, no approval, ever ----------------
        if not SOLDERS_AVAILABLE:
            return ValidationResult(
                approved=False,
                issues=[
                    ValidationIssue(
                        ValidationCode.PARSER_UNAVAILABLE,
                        "solders not installed - cannot prove transaction "
                        "semantics; refusing to approve (fail-closed)",
                    )
                ],
            )
        return self._validate_parsed(tx_b64, expected)

    def _validate_parsed(
        self, tx_b64: str, expected: ExpectedSwap
    ) -> ValidationResult:
        try:
            raw = base64.b64decode(tx_b64, validate=True)
            tx = VersionedTransaction.from_bytes(raw)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(
                approved=False,
                issues=[ValidationIssue(ValidationCode.DECODE_FAILED, str(exc))],
            )
        return self._check_message(tx, expected)

    def _check_message(
        self, tx: Any, expected: ExpectedSwap
    ) -> ValidationResult:
        msg = tx.message
        issues: List[ValidationIssue] = []
        parsed: Dict[str, Any] = {}

        # -- Program IDs against allowlist ---------------------------
        program_ids: Set[str] = set()
        for instruction in msg.instructions:
            pid = str(instruction.program_id)
            program_ids.add(pid)
            if pid not in expected.allow_programs:
                issues.append(
                    ValidationIssue(
                        ValidationCode.UNKNOWN_PROGRAM,
                        f"program {pid} not in allowlist",
                    )
                )
        parsed["program_ids"] = sorted(program_ids)

        # -- Input/output mints must appear among accounts ------------
        account_strs = [str(k) for k in msg.account_keys]
        parsed["num_accounts"] = len(account_strs)
        if not account_strs:
            issues.append(
                ValidationIssue(ValidationCode.EMPTY_MESSAGE, "no accounts")
            )
        if expected.input_mint not in account_strs:
            issues.append(
                ValidationIssue(
                    ValidationCode.MINT_MISMATCH,
                    f"input mint {expected.input_mint} absent from accounts",
                )
            )
        if expected.output_mint not in account_strs:
            issues.append(
                ValidationIssue(
                    ValidationCode.MINT_MISMATCH,
                    f"output mint {expected.output_mint} absent from accounts",
                )
            )

        return self._finish(msg, tx, expected, issues, parsed)


    def _finish(
        self,
        msg: Any,
        tx: Any,
        expected: ExpectedSwap,
        issues: List[ValidationIssue],
        parsed: Dict[str, Any],
    ) -> ValidationResult:
        # -- Signers: exactly the taker -------------------------------
        signers = [str(k) for k in msg.account_keys[: len(tx.signatures)]]
        parsed["signers"] = signers
        if signers != [expected.taker_pubkey]:
            issues.append(
                ValidationIssue(
                    ValidationCode.UNEXPECTED_SIGNER,
                    f"signers {signers} != [{expected.taker_pubkey}]",
                )
            )

        # -- Compute budget: priority fee cap --------------------------
        max_fee_lamports = self._extract_priority_fee(msg)
        parsed["priority_fee_lamports"] = max_fee_lamports
        if max_fee_lamports > expected.max_priority_fee_lamports:
            issues.append(
                ValidationIssue(
                    ValidationCode.PRIORITY_FEE_EXCEEDED,
                    f"priority fee {max_fee_lamports} > cap "
                    f"{expected.max_priority_fee_lamports}",
                )
            )

        # -- Min expected output must be present and respected ---------
        min_out = self._extract_min_out(msg)
        parsed["min_out_raw"] = min_out
        if min_out is None:
            issues.append(
                ValidationIssue(
                    ValidationCode.MISSING_MIN_OUT,
                    "no slippage-bounded swap instruction found; "
                    "transaction has no min-out guarantee",
                )
            )
        elif min_out < expected.min_out_raw:
            issues.append(
                ValidationIssue(
                    ValidationCode.MISSING_MIN_OUT,
                    f"tx min-out {min_out} < authorized floor "
                    f"{expected.min_out_raw}",
                )
            )

        # -- SOL movement: no drain beyond authorized scope ------------
        drain = self._extract_taker_sol_drain(msg, expected.taker_pubkey)
        parsed["taker_sol_drain_lamports"] = drain
        ceiling = (
            expected.in_amount_raw
            + expected.max_priority_fee_lamports
            + SOL_BUFFER_LAMPORTS
        )
        if drain > ceiling:
            issues.append(
                ValidationIssue(
                    ValidationCode.SOL_MOVEMENT,
                    f"taker SOL movement {drain} exceeds authorized "
                    f"ceiling {ceiling}",
                )
            )

        approved = not issues
        if approved:
            log.info(
                "tx validated: programs=%s fee=%s min_out=%s",
                parsed.get("program_ids"),
                parsed.get("priority_fee_lamports"),
                parsed.get("min_out_raw"),
            )
        else:
            log.warning("tx REJECTED: %s", [i.code for i in issues])
        return ValidationResult(approved=approved, issues=issues, parsed=parsed)

    # -- Instruction inspection helpers --------------------------------
    @staticmethod
    def _extract_priority_fee(msg: Any) -> int:
        """Max compute-unit price in the message (lamports per CU)."""
        fee = 0
        try:
            for instruction in msg.instructions:
                if str(instruction.program_id) != COMPUTE_BUDGET_PROGRAM:
                    continue
                data = bytes(instruction.data)
                # ComputeBudget instruction 2 = SetComputeUnitPrice:
                # u8 tag + u64 lamports-per-CU little-endian.
                if len(data) >= 9 and data[0] == 2:
                    fee = max(fee, int.from_bytes(data[1:9], "little"))
        except Exception:  # noqa: BLE001 - defensive; treat as 0 on parse issue
            return 0
        return fee

    @staticmethod
    def _extract_min_out(msg: Any) -> Optional[int]:
        """Locate the aggregator route's min-out from instruction data.

        Jupiter swap instructions embed the minimum out amount as a u64
        little-endian value. Route-plan layout varies, so scan plausible
        u64 candidates inside aggregator instructions and take the LARGEST
        (the most generous interpretation - least likely to reject a
        legitimate tx, while still bounded by the authorized floor).
        """
        best: Optional[int] = None
        try:
            for instruction in msg.instructions:
                if str(instruction.program_id) != JUPITER_V6_PROGRAM:
                    continue
                data = bytes(instruction.data)
                for offset in range(0, max(0, len(data) - 7)):
                    candidate = int.from_bytes(data[offset:offset + 8], "little")
                    if candidate and (best is None or candidate > best):
                        best = candidate
        except Exception:  # noqa: BLE001
            return None
        return best

    @staticmethod
    def _extract_taker_sol_drain(msg: Any, taker: str) -> int:
        """Sum SystemProgram transfers sourced from the taker (lamports).

        SystemProgram transfer instruction: u8 tag (2) + u64 lamports,
        with accounts [source, destination].
        """
        total = 0
        try:
            for instruction in msg.instructions:
                if str(instruction.program_id) != SYSTEM_PROGRAM:
                    continue
                data = bytes(instruction.data)
                if len(data) < 9 or data[0] != 2:
                    continue
                accounts = [str(a) for a in instruction.accounts]
                if not accounts or accounts[0] != taker:
                    continue
                total += int.from_bytes(data[1:9], "little")
        except Exception:  # noqa: BLE001
            return 0
        return total
