"""Property-based invariant tests (review item 19).

The rules of money software tested here must hold ALWAYS, for every input:

    * balance can never become NaN / Inf
    * size can never be negative or non-finite
    * risk rejection can never execute
    * kill switch can never execute
    * ledger positions must reconcile to balance changes
    * trade can never exceed allocated capital
    * failed leg must create a recovery state
    * PnL ledger must reconcile to balance changes
"""