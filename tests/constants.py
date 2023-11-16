from algojig import TealishProgram


vault_approval_program = TealishProgram('contracts/vault/vault_approval.tl')
vault_clear_state_program = TealishProgram('contracts/vault/vault_clear_state.tl')

staking_voting_approval_program = TealishProgram('contracts/staking_voting/staking_voting_approval.tl')
staking_voting_clear_state_program = TealishProgram('contracts/staking_voting/staking_voting_clear_state.tl')

proposal_voting_approval_program = TealishProgram('contracts/proposal_voting/proposal_voting_approval.tl')
proposal_voting_clear_state_program = TealishProgram('contracts/proposal_voting/proposal_voting_clear_state.tl')

rewards_approval_program = TealishProgram('contracts/rewards/rewards_approval.tl')
rewards_clear_state_program = TealishProgram('contracts/rewards/rewards_clear_state.tl')

arbitrary_executor_approval_program = TealishProgram('contracts/arbitrary_executor/arbitrary_executor_approval.tl')
arbitrary_executor_clear_state_program = TealishProgram('contracts/arbitrary_executor/arbitrary_executor_clear_state.tl')

TINY_ASSET_ID = 12345

VAULT_APP_ID = 6000
REWARDS_APP_ID = 7000
STAKING_VOTING_APP_ID = 8000
PROPOSAL_VOTING_APP_ID = 9000
ARBITRARY_EXECUTOR_APP_ID = 10000
