from tinyman.governance.constants import DAY
from tinyman.governance.proposal_voting.storage import ProposalVotingAppGlobalState

from tests.constants import PROPOSAL_VOTING_APP_ID


def get_rewards_app_global_state(ledger):
    return ProposalVotingAppGlobalState(**{key.decode(): value for key, value in ledger.global_states[PROPOSAL_VOTING_APP_ID].items()})

def get_end_timestamp_of_day(value):
    return ((value // DAY) * DAY) + DAY