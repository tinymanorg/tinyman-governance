# from tinyman.governance.staking_voting.storage import StakingVotingAppGlobalState
# 
# from tests.constants import STAKING_VOTING_APP_ID
# 
# 
# def get_staking_voting_app_global_state(ledger):
#     return StakingVotingAppGlobalState(**{key.decode(): value for key, value in ledger.global_states[STAKING_VOTING_APP_ID].items()})

# def is_account_attendance_box_exists(ledger, address, proposal_index):
#     box_index = proposal_index // STAKING_ACCOUNT_ATTENDANCE_SHEET_BOX_SIZE
#     account_attendance_box_name = get_staking_attendance_sheet_box_name(address, box_index)
#     return account_attendance_box_name not in ledger.boxes[STAKING_VOTING_APP_ID]

# def get_new_asset_count(ledger, proposal_index, asset_ids):
#     new_asset_count = 0
#     for asset_id in asset_ids:
#         box_name = get_staking_vote_box_name(proposal_index, asset_id)
#         if box_name not in ledger.boxes[STAKING_VOTING_APP_ID]:
#             new_asset_count += 1
#     return new_asset_count
