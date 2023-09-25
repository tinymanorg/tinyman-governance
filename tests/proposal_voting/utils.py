from tinyman.governance.constants import DAY
from tinyman.governance.proposal_voting.storage import ProposalVotingAppGlobalState

from tests.constants import PROPOSAL_VOTING_APP_ID


def get_proposal_voting_app_global_state(ledger, app_id=None):
    if app_id is None:
        app_id = PROPOSAL_VOTING_APP_ID
    return ProposalVotingAppGlobalState(**{key.decode(): value for key, value in ledger.global_states[app_id].items()})

def get_end_timestamp_of_day(value):
    return ((value // DAY) * DAY) + DAY