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

TINY_ASSET_ID = 12345

LOCKING_APP_ID = 6000
REWARDS_APP_ID = 7000
STAKING_VOTING_APP_ID = 8000
PROPOSAL_VOTING_APP_ID = 9000
