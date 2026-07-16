from __future__ import annotations

import socket
import threading
import time
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, expect

from ota_simulator.api import create_app
from ota_simulator.repository import SQLiteRepository
from ota_simulator.service import SimulatorService


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    service = SimulatorService(SQLiteRepository(tmp_path / "browser.db"))
    config = uvicorn.Config(
        create_app(service=service),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    health_url = f"http://127.0.0.1:{port}/health"
    for _ in range(100):
        try:
            with urllib.request.urlopen(health_url, timeout=0.2) as response:
                if response.status == 200:
                    break
        except OSError:
            time.sleep(0.05)
    else:
        server.should_exit = True
        thread.join(timeout=5)
        pytest.fail("test server did not start")

    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.mark.e2e
def test_dashboard_update_fault_rollback_and_reset_flow(page: Page, live_server: str) -> None:
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: console_errors.append(message.text) if message.type == "error" else None,
    )

    page.goto(live_server)

    expect(page.get_by_role("heading", name="Fleet command center")).to_be_visible()
    expect(page.locator("#controller-rows tr")).to_have_count(6)
    expect(page.locator("#total-count")).to_have_text("6")
    expect(page.get_by_role("heading", name="Telematics", exact=True)).to_be_visible()

    page.get_by_label("Target version").fill("2.1.0")
    page.locator("#update-form").get_by_role("button", name="Run update").click()
    expect(page.locator("#toast")).to_contain_text("Updated Telematics to 2.1.0")
    expect(page.locator("#current-version")).to_have_text("Current version: 2.1.0")
    expect(page.get_by_role("button", name="Rollback")).to_be_enabled()
    expect(page.locator("#event-log")).to_contain_text("UPDATE_COMPLETED")

    page.get_by_label("Fault scenario").select_option("install_failure")
    page.get_by_label("Target version").fill("2.2.0")
    page.locator("#update-form").get_by_role("button", name="Run update").click()
    expect(page.locator("#dtc-list")).to_contain_text("U3000")
    expect(page.locator("#event-log")).to_contain_text("automatic rollback")

    page.get_by_role("button", name="Reset lab").click()
    expect(page.locator("#current-version")).to_have_text("Current version: 2.0.4")
    expect(page.locator("#total-count")).to_have_text("6")

    page.set_viewport_size({"width": 390, "height": 844})
    expect(page.get_by_role("heading", name="Fleet command center")).to_be_visible()
    primary_surfaces_fit = page.evaluate(
        """() => ['.summary-rail', '.workspace-grid', '.timeline-panel'].every((selector) => {
          const rect = document.querySelector(selector).getBoundingClientRect();
          return rect.left >= 0 && rect.right <= document.documentElement.clientWidth;
        })"""
    )
    assert primary_surfaces_fit is True
    assert console_errors == []
