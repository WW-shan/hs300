import { describe, expect, it } from 'vitest'
import { strategyResultCandidate } from './researchCandidates'

describe('strategyResultCandidate', () => {
  it('preserves the position policy needed to reproduce a strategy backtest', () => {
    const candidate = strategyResultCandidate({
      config: {
        strategy_id: 'ai_hs300_cash_safety_v14',
        position_sizing: 'policy',
        position_policy: 'up_vol_target_14',
        position_policy_params: { target: 0.14, lookback: 60, floor: 0.5 },
      },
      stats: {},
      strategy_info: { name: 'Cash Safety' },
      elapsed_ms: 1,
    } as never)

    expect(candidate.config.position_policy).toBe('up_vol_target_14')
    expect(candidate.config.position_policy_params).toEqual({
      target: 0.14,
      lookback: 60,
      floor: 0.5,
    })
  })
})
