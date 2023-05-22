from algojig import TealishProgram


locking_approval_program = TealishProgram('contracts/locking/locking_approval.tl')
locking_clear_state_program = TealishProgram('contracts/locking/locking_clear_state.tl')

voting_approval_program = TealishProgram('contracts/voting/voting_approval.tl')
voting_clear_state_program = TealishProgram('contracts/voting/voting_clear_state.tl')

HOUR = 60 * 60
DAY = 24 * HOUR
WEEK = 7 * DAY
MAX_LOCK_TIME = 4 * 365 * DAY
MAX_OPTION_COUNT = 16

TOTAL_POWERS = b"total_powers"
SLOPE_CHANGES = b"slope_changes"

PROPOSALS = b"proposals"
VOTES = b"votes"
