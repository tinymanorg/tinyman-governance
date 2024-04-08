import math

from tinyman.governance.rewards.constants import REWARD_HISTORY_BOX_ARRAY_LEN, REWARD_PERIOD_BOX_ARRAY_LEN
from tinyman.governance.rewards.storage import RewardsAppGlobalState, get_reward_history_box_name, get_reward_period_box_name, parse_box_reward_period, parse_box_reward_history, RewardHistory

from tests.constants import REWARDS_APP_ID


def get_rewards_app_global_state(ledger, app_id=None):
    if app_id is None:
        app_id = REWARDS_APP_ID
    return RewardsAppGlobalState(**{key.decode(): value for key, value in ledger.global_states[app_id].items()})


def get_reward_histories(ledger) -> list[RewardHistory]:
    reward_history_count = get_rewards_app_global_state(ledger).reward_history_count

    if reward_history_count:
        box_count = math.ceil(reward_history_count / REWARD_HISTORY_BOX_ARRAY_LEN)
    else:
        box_count = 0

    reward_histories = []
    for box_index in range(box_count):
        box_name = get_reward_history_box_name(box_index=box_index)
        raw_box = ledger.boxes[REWARDS_APP_ID][box_name]
        reward_histories.extend(parse_box_reward_history(raw_box))
    return reward_histories


def get_reward_periods(ledger):
    reward_period_count = get_rewards_app_global_state(ledger).reward_period_count

    box_count = math.ceil(reward_period_count / REWARD_PERIOD_BOX_ARRAY_LEN)

    reward_periods = []
    for box_index in range(box_count):
        box_name = get_reward_period_box_name(box_index=box_index)
        raw_box = ledger.boxes[REWARDS_APP_ID][box_name]
        reward_periods.extend(parse_box_reward_period(raw_box))
    return reward_periods
