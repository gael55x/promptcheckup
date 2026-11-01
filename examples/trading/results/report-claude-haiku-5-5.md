Not sure yet: the confidence interval includes zero. Add more cases or let it run again.

| n | Mean | 95% CI | Wins | Losses | Ties |
| --- | --- | --- | --- | --- | --- |
| 60 | +12.76 | -11.04 to +36.57 | 26 | 13 | 21 |

Largest changes (first successful paired sample shown):

| Case | Difference | A | B |
| --- | --- | --- | --- |
| b9675d4e266c | +560.00 | {"position": -1.0, "reason": "The 5-day return is -9.89%, which is negative, so the trend signal is short. Position size is min(1, \|-0.0989\| / 0.05) = min(1, 1.98) = 1, so the position is full short at -1."} | {"position": 0.0, "reason": "The 5-day return is -9.9%, which would signal a short of size 1 (capped at 1 since 0.099/0.05 &gt; 1). However, 20-day daily volatility is 4.1%, above the 2% threshold, so the policy requires staying flat."} |
| 2841cf9d151f | +275.88 | {"position": -0.6023, "reason": "The 5-day return is -3.01%, which is negative, so the trend-following policy calls for a short position. Its size is the smaller of 1 and \|return_5d\| / 0.05 = 0.0301 / 0.05 = 0.6023, so the position is -0.6023. The 5-day return checks out against the closes: 79.396 /… | {"position": 0.0, "reason": "The 5-day return of -3.01% gives a short signal, with a raw size of min(1, 0.0301/0.05), about 0.60. However, the 20-day volatility of 2.67% exceeds the 2% limit, so the policy requires staying flat. The last day's +3.05% move does not trigger the 4% drop rule, but the v… |
| ff151ec1d50c | -219.58 | {"position": -0.8491, "reason": "The 5-day return is -4.25%, which is negative, so the trend-following rule calls for a short. The size is min(1, \|−0.04246\| / 0.05) = 0.8491, so the position is -0.8491. The 5-day return matches the last 5 closes (73.86 vs. 77.13 five days earlier)."} | {"position": 0.0, "reason": "The trend signal is short, since the 5-day return is -4.25%, which would give a raw size of about -0.85. The risk overrides stay flat: 20-day volatility is 4.16%, above the 2% limit, and the last day fell 5.5%, beyond the 4% limit. Position is therefore zero."} |

Calls: 120 agent, 0 judge. Tokens: unavailable (judge); unavailable (agent protocol). Judge model: not reported.

Prompt hashes: A: 3f4b4b039d36e74a; B: 3612022f3c95bbd1.

promptcheckup version: 0.0.1.
