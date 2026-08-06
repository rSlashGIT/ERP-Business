import numpy as np


class ParameterizedSSPolicy:
    """
    Parameterized (s,S) policy with context-dependent thresholds.

    s(context) = s_base + w1*demand_forecast + w2*demand_std + w3*lead_time
    S(context) = S_base + w4*demand_forecast + w5*demand_std + w6*lead_time

    theta = [s_base, S_base, w1, w2, w3, w4, w5, w6]

    Default theta_0 reduces to classical (s,S) with s=10, S=30.
    """

    # s_base: normalized inventory threshold (tactical_state[0] is inv/1500 ∈ [0,3])
    # S_base: target in action-index space (env.action_map has indices 0..7)
    DEFAULT_THETA = np.array([1.5, 5.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                             dtype=np.float64)

    def __init__(self, theta: np.ndarray = None):
        self.theta = self.DEFAULT_THETA.copy() if theta is None else np.array(theta, dtype=np.float64)

    def get_params(self) -> np.ndarray:
        return self.theta.copy()

    def set_params(self, theta: np.ndarray) -> None:
        self.theta = np.array(theta, dtype=np.float64)

    def act(self, state: dict) -> int:
        inv   = float(state.get('inventory_level', 0.0))
        d_hat = float(state.get('demand_forecast', 0.0))
        d_std = float(state.get('demand_std', 0.0))
        lt    = float(state.get('lead_time', 0.0))

        s_base, S_base = self.theta[0], self.theta[1]
        w1, w2, w3     = self.theta[2], self.theta[3], self.theta[4]
        w4, w5, w6     = self.theta[5], self.theta[6], self.theta[7]

        s_ctx = s_base + w1 * d_hat + w2 * d_std + w3 * lt
        S_ctx = S_base + w4 * d_hat + w5 * d_std + w6 * lt

        # inventory_on_order not tracked by env; treat as 0
        position = inv
        if position < s_ctx:
            qty = max(0.0, S_ctx - position)
        else:
            qty = 0.0

        return int(np.clip(round(qty), 0, 7))

    def reset(self) -> None:
        pass


if __name__ == '__main__':
    policy = ParameterizedSSPolicy()
    state = {
        'inventory_level': 5.0,
        'demand_forecast': 8.0,
        'demand_std': 2.0,
        'lead_time': 3.0,
    }
    qty = policy.act(state)
    print(f"theta_0 params : {policy.get_params()}")
    print(f"order quantity : {qty}")
    assert isinstance(qty, int) and qty >= 0, "order quantity must be a non-negative int"
    print("Sanity check passed.")
