### Learning from incidents

| run | incidents | avg steps | harmful % | correct outcome % |
|---|---|---|---|---|
| stream, no memory (first half) | 10 | 8.9 | 20.0 | 100.0 |
| stream, no memory (second half) | 11 | 9 | 45.5 | 100.0 |
| stream, learning (first half) | 10 | 8 | 10.0 | 100.0 |
| stream, learning (second half) | 11 | 7.7 | 0.0 | 100.0 |
| unseen test, no memory | 21 | 9.0 | 33.3 | 100.0 |
| unseen test, learned memory | 21 | 7.7 | 0.0 | 100.0 |
| unseen test, best fixed strategy | 21 | 7.7 | 0.0 | 100.0 |

What it learned:
- hung_worker: use 'targeted' (5/5 fixed, 3.0 steps, 0 harmful actions)
- queue_loss: use 'targeted' (6/6 fixed, 3.0 steps, 0 harmful actions)
- silent_drop: use 'targeted' (7/7 fixed, 2.0 steps, 0 harmful actions)
- sync_corruption: use 'targeted' (5/5 fixed, 3.0 steps, 0 harmful actions)
