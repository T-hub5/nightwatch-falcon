### Forecasting

**1. Queue saturation warning (before the first lost message)**

- 14 of 14 queue-loss incidents were warned before the first loss
- median lead time: 30.0 orders
- capacity warnings on healthy systems with no fault (a busy queue warns even without a fault): burst 1: 0/10, burst 2: 10/10, burst 4: 10/10

**2. Acting on the forecast (drain the queue when saturation is predicted)**

| mode | incidents | no message loss % | avg lost messages |
|---|---|---|---|
| no action (monitor only) | 14 | 0.0 | 175.9 |
| drain on warning | 14 | 100.0 | 0 |

**3. Projected loss 150 orders ahead (checked at order 150, scored at order 300)**

| fault | cases | forecast error % | 'no change' error % |
|---|---|---|---|
| silent_drop | 8 | 20.9 | 53.9 |
| sync_corruption | 8 | 19.2 | 55.6 |

**4. Faults these signals cannot forecast**

- hung_worker: any warning before the failure in 2 of 6 cases (these are generic queue-capacity warnings, not a signal for this fault)
- provider_down: any warning before the failure in 2 of 6 cases (these are generic queue-capacity warnings, not a signal for this fault)
