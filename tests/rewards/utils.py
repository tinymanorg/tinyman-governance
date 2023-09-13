from tinyman.governance.rewards.storage import RewardsAppGlobalState

from tests.constants import REWARDS_APP_ID


def get_rewards_app_global_state(ledger):
    return RewardsAppGlobalState(**{key.decode(): value for key, value in ledger.global_states[REWARDS_APP_ID].items()})