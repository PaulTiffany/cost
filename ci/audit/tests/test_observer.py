"""AAA tests for the typed Observer class."""

from ci.audit.observer import Observer, _default_adjust


def test_drift_history_appends_in_order():
    # Arrange
    obs = Observer(name="A")

    # Act
    obs.record_drift(0.1)
    obs.record_drift(0.5)
    obs.record_drift(0.3)

    # Assert
    assert obs.drift_history == [0.1, 0.5, 0.3]


def test_step_appends_drift_and_adjusts_resolution_down_when_drift_high():
    # Arrange
    obs = Observer(name="A", resolution=1.0)

    # Act
    new_res = obs.step(drift=2.0)

    # Assert
    assert obs.drift_history == [2.0]
    assert new_res == 0.5
    assert obs.resolution == 0.5


def test_step_relaxes_resolution_when_drift_low():
    # Arrange
    obs = Observer(name="A", resolution=1.0)

    # Act
    new_res = obs.step(drift=0.1)

    # Assert
    assert obs.drift_history == [0.1]
    assert new_res == 1.05
    assert obs.resolution == 1.05


def test_mean_drift_returns_none_when_empty():
    # Arrange
    obs = Observer(name="A")

    # Act / Assert
    assert obs.mean_drift() is None


def test_mean_drift_returns_average_of_history():
    # Arrange
    obs = Observer(name="A")
    obs.record_drift(0.0)
    obs.record_drift(2.0)

    # Act
    m = obs.mean_drift()

    # Assert
    assert m == 1.0


def test_default_adjust_function_directly():
    # Arrange / Act
    high = _default_adjust(1.0, 5.0)
    low = _default_adjust(1.0, 0.5)

    # Assert
    assert high == 0.5
    assert low == 1.05


def test_custom_adjust_resolution_callable_is_used():
    # Arrange
    obs = Observer(
        name="A",
        resolution=2.0,
        adjust_resolution=lambda current, drift: current + drift,
    )

    # Act
    new_res = obs.step(drift=3.0)

    # Assert
    assert new_res == 5.0
    assert obs.drift_history == [3.0]
