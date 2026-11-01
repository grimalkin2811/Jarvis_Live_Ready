from types import SimpleNamespace

from UI.dashboard.actions import conversation_new, routine_names, run_routine


def test_dashboard_actions_resolve_injected_services():
    calls = []
    context = SimpleNamespace(start_new_conversation=lambda reason: calls.append(reason) or {'conversation_id': '1'})
    routines = SimpleNamespace(
        list_routine_names=lambda: {'success': True, 'routines': [{'name': 'Test', 'enabled': True}]},
        run_routine=lambda name: {'success': name == 'Test'},
    )
    services = {'conversation': context, 'routines': routines}
    assert conversation_new(services.__getitem__).success
    assert calls == ['Dashboard']
    assert routine_names(services.__getitem__) == ['Test']
    assert run_routine('Test', services.__getitem__).success
    assert not conversation_new({}.__getitem__).success
