import sys
sys.path.insert(0, 'src')
import numpy as np
from multi_echelon import SupplyChainNetwork, split_theta24, _load_m5_series

theta_loaded = np.load('models/uniform_best_theta_robust.npy')
print(f'theta_loaded shape: {theta_loaded.shape}')
print(f'first 8 values: {theta_loaded[:8]}')
if theta_loaded.size == 8:
    theta24 = np.concatenate([theta_loaded, theta_loaded, theta_loaded])
else:
    theta24 = theta_loaded
thetas = split_theta24(theta24)

series = _load_m5_series()
net = SupplyChainNetwork(thetas=thetas, demand_series=series, seed=42)
summary = net.simulate(90)

print()
print('=== Warehouse breakdown ===')
wh = net.warehouse
print(f'Warehouse holding cost: ${wh.metrics.holding_cost:,.2f}')
print(f'Warehouse procurement cost: ${wh.metrics.procurement_cost:,.2f}')
print(f'Warehouse stockout/backlog cost: ${wh.metrics.stockout_cost:,.2f}')
print(f'Warehouse final inventory: {wh.inventory:.0f}')
print(f'Warehouse orders placed: {wh.metrics.orders_placed}')

records = net._daily_records
wh_inv = [r.get('warehouse_inventory', 0) for r in records]
if wh_inv:
    print(f'Warehouse inventory over time: min={min(wh_inv):.0f} '
          f'max={max(wh_inv):.0f} mean={np.mean(wh_inv):.0f}')

print()
print('=== Store breakdown ===')
total_store_holding = 0
total_store_procurement = 0
for sid in 'ABC':
    s = net.stores[sid]
    sl = s.metrics.sales_units / max(s.metrics.demand_units, 1)
    total_store_holding += s.metrics.holding_cost
    total_store_procurement += s.metrics.procurement_cost
    print(f'Store {sid}:')
    print(f'  demand={s.metrics.demand_units:.0f} sales={s.metrics.sales_units:.0f} SL={sl:.1%}')
    print(f'  final_inv={s.inventory:.0f}')
    print(f'  holding=${s.metrics.holding_cost:,.2f}')
    print(f'  procurement=${s.metrics.procurement_cost:,.2f}')

print()
print('=== Cost summary ===')
print(f'Total store holding: ${total_store_holding:,.2f}')
print(f'Warehouse holding: ${wh.metrics.holding_cost:,.2f}')
print(f'Total holding: ${total_store_holding + wh.metrics.holding_cost:,.2f}')
print(f'Total store procurement: ${total_store_procurement:,.2f}')
print(f'Warehouse procurement: ${wh.metrics.procurement_cost:,.2f}')
print()
print(f'Network total profit: ${summary["network_total_profit"]:,.2f}')
print(f'Network service level: {summary["service_level"]:.1%}')
