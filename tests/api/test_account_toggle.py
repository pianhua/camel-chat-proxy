"""账号启停与代理配置测试。"""

import pytest
from app.core.config import config_manager, CamelAccount


def test_account_toggle_and_scheduling():
    config_manager.config.camel_accounts = [
        CamelAccount(email="acc1@a.com", enabled=True, is_valid=True),
        CamelAccount(email="acc2@a.com", enabled=False, is_valid=True),
    ]

    # get_next_account should only return acc1 because acc2 is disabled
    next_acc = config_manager.get_next_account()
    assert next_acc is not None
    assert next_acc.email == "acc1@a.com"

    # toggle acc1 off and acc2 on
    config_manager.config.camel_accounts[0].enabled = False
    config_manager.config.camel_accounts[1].enabled = True

    next_acc2 = config_manager.get_next_account()
    assert next_acc2 is not None
    assert next_acc2.email == "acc2@a.com"
