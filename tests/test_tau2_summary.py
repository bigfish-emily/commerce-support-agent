from scripts.summarize_tau2_results import summarize


def test_tau2_summary_computes_pass_k_and_failures() -> None:
    simulations = [
        {
            "task_id": "task_a",
            "termination_reason": "user_stop",
            "duration": 10.0,
            "agent_cost": 0.01,
            "reward_info": {"reward": 1.0},
        },
        {
            "task_id": "task_a",
            "termination_reason": "user_stop",
            "duration": 20.0,
            "agent_cost": 0.02,
            "reward_info": {"reward": 0.0},
        },
        {
            "task_id": "task_b",
            "termination_reason": "agent_stop",
            "duration": 30.0,
            "agent_cost": 0.03,
            "reward_info": {"reward": 0.0},
        },
        {
            "task_id": "task_b",
            "termination_reason": "agent_stop",
            "duration": 40.0,
            "agent_cost": 0.04,
            "reward_info": {"reward": 0.0},
        },
        {
            "task_id": "task_c",
            "termination_reason": "infrastructure_error",
            "reward_info": None,
        },
    ]

    result = summarize(simulations)

    assert result["total_simulations"] == 5
    assert result["evaluated_simulations"] == 4
    assert result["total_tasks"] == 2
    assert result["avg_reward"] == 0.25
    assert result["pass_hat_ks"]["pass^1"] == 0.25
    assert result["pass_hat_ks"]["pass^2"] == 0.0
    assert result["termination_counts"] == {
        "agent_stop": 2,
        "infrastructure_error": 1,
        "user_stop": 2,
    }
    assert result["failed_task_ids"] == ["task_b"]
