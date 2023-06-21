from algojig import TealishProgram


locking_approval_program = TealishProgram('contracts/locking/locking_approval.tl')
locking_clear_state_program = TealishProgram('contracts/locking/locking_clear_state.tl')

staking_voting_approval_program = TealishProgram('contracts/staking_voting/staking_voting_approval.tl')
staking_voting_clear_state_program = TealishProgram('contracts/staking_voting/staking_voting_clear_state.tl')

proposal_voting_approval_program = TealishProgram('contracts/proposal_voting/proposal_voting_approval.tl')
proposal_voting_clear_state_program = TealishProgram('contracts/proposal_voting/proposal_voting_clear_state.tl')

rewards_approval_program = TealishProgram('contracts/rewards/rewards_approval.tl')
rewards_clear_state_program = TealishProgram('contracts/rewards/rewards_clear_state.tl')

HOUR = 60 * 60
DAY = 24 * HOUR
WEEK = 7 * DAY
MAX_LOCK_TIME = 4 * 365 * DAY
MAX_OPTION_COUNT = 16

TWO_TO_THE_64 = 2 ** 64

TOTAL_POWERS = b'tp'
SLOPE_CHANGES = b'sc'

REWARD_HISTORY = b'rh'

PROPOSAL_BOX_PREFIX = b'p'
VOTE_BOX_PREFIX = b'v'
ATTENDANCE_BOX_PREFIX = b'a'

# 100_000 Default
# 100_000 Opt-in
# Box
# https://developer.algorand.org/docs/get-details/dapps/smart-contracts/apps/?from_query=box#minimum-balance-requirement-for-boxes
# 2500 + 400 * (len(n)+s)
# 2_500 Box
# 411_200 = 400 * (20 + 1008)
LOCKING_APP_MINIMUM_BALANCE_REQUIREMENT = 613_700


ACCOUNT_STATE_SIZE = 24
SLOPE_CHANGE_SIZE = 16

ACCOUNT_POWER_SIZE = 48
ACCOUNT_POWER_BOX_SIZE = 1008
ACCOUNT_POWER_BOX_ARRAY_LEN = 21

TOTAL_POWER_SIZE = 48
TOTAL_POWER_BOX_SIZE = 1008
TOTAL_POWER_BOX_ARRAY_LEN = 21

REWARD_HISTORY_SIZE = 16
REWARD_HISTORY_BOX_SIZE = 1024
REWARD_HISTORY_BOX_ARRAY_LEN = 64


# 100_000 Default
# 100_000 Opt-in
# Box
# https://developer.algorand.org/docs/get-details/dapps/smart-contracts/apps/?from_query=box#minimum-balance-requirement-for-boxes
# 2500 + 400 * (len(n)+s)
# 2_500 Box
# 413_600 = 400 * (10 + 1024)
REWARDS_APP_MINIMUM_BALANCE_REQUIREMENT = 616_100
