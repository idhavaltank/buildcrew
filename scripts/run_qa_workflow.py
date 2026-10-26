#!/usr/bin/env python3
"""Run a QA packet through explicitly registered host adapters."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import sys

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from engine.qa_adapters import AndroidQAAdapter, BrowserQAAdapter, FlutterQAAdapter, IOSSimulatorQAAdapter, unavailable_manifest  # noqa: E402
from engine.qa_runner import AdapterRegistry, run_qa_workflow  # noqa: E402
from engine.real_qa_adapters import (  # noqa: E402
    ADBAndroidTransport,
    FlutterRuntimeTransport,
    IOSSimulatorTransport,
    PlaywrightBrowserTransport,
    android_manifest,
    browser_manifest,
    flutter_manifest,
    ios_manifest,
)


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workflow", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--browser-manifest", type=Path)
    parser.add_argument("--android-manifest", type=Path)
    parser.add_argument("--playwright-node-modules", type=Path, help="Approved Node modules root containing Playwright")
    parser.add_argument("--browser-origin", action="append", default=[], help="Approved browser origin; repeat for multiple origins")
    parser.add_argument("--browser-executable", type=Path, help="Approved browser executable, for example /usr/bin/google-chrome")
    parser.add_argument("--browser-name", choices=["chromium", "firefox", "webkit"], default="chromium")
    parser.add_argument("--browser-artifacts-dir", type=Path, help="Explicit directory for browser evidence artifacts")
    parser.add_argument("--browser-headful", action="store_true", help="Run the approved browser visibly")
    parser.add_argument("--android-serial", help="Explicit ADB emulator/device serial")
    parser.add_argument("--adb-path", default="adb", help="ADB executable path")
    parser.add_argument("--android-apk", type=Path, help="Explicit APK path for an install workflow step")
    parser.add_argument("--android-package", help="Explicit Android application package for launch/clear_data")
    parser.add_argument("--android-activity", help="Reserved activity identity for the host receipt")
    parser.add_argument("--android-artifacts-dir", type=Path, help="Explicit directory for Android evidence artifacts")
    parser.add_argument("--ios-manifest", type=Path)
    parser.add_argument("--ios-udid", help="Explicit booted iOS Simulator UDID")
    parser.add_argument("--xcrun-path", default="xcrun", help="xcrun executable path")
    parser.add_argument("--ios-app", type=Path, help="Explicit .app path for an install workflow step")
    parser.add_argument("--ios-bundle-id", help="Explicit iOS application bundle identifier for launch")
    parser.add_argument("--ios-artifacts-dir", type=Path, help="Explicit directory for iOS evidence artifacts")
    parser.add_argument("--flutter-manifest", type=Path)
    parser.add_argument("--flutter-project-root", type=Path, help="Flutter project root")
    parser.add_argument("--flutter-device", help="Explicit Flutter device identifier")
    parser.add_argument("--flutter-platform", choices=["android", "ios"], default="android")
    parser.add_argument("--flutter-path", default="flutter", help="Flutter executable path")
    parser.add_argument("--flutter-artifacts-dir", type=Path, help="Explicit directory for Flutter evidence artifacts")
    args = parser.parse_args(argv)
    workflow = _load(args.workflow)
    packet = _load(args.packet)
    host_id = socket.gethostname()

    browser_transport = None
    if args.playwright_node_modules:
        if args.browser_manifest:
            parser.error("--playwright-node-modules cannot be combined with --browser-manifest")
        if not args.browser_origin:
            parser.error("--playwright-node-modules requires at least one --browser-origin")
        if not args.browser_artifacts_dir:
            parser.error("--playwright-node-modules requires --browser-artifacts-dir")
        target_names = packet.get("targetAllowlist", {}).get("webTargets", ["browser-target"])
        browser_manifest_value = browser_manifest(adapter_id="playwright-browser", host_id=host_id, target_allowlist=target_names)
        browser_transport = PlaywrightBrowserTransport(
            node_modules_root=args.playwright_node_modules,
            artifacts_dir=args.browser_artifacts_dir,
            allowed_origins=args.browser_origin,
            browser_name=args.browser_name,
            executable_path=args.browser_executable,
            headless=not args.browser_headful,
        )
    else:
        browser_manifest_value = _load(args.browser_manifest) if args.browser_manifest else unavailable_manifest(adapter_id="browser-host-adapter", kind="browser")

    android_transport = None
    if args.android_serial:
        if args.android_manifest:
            parser.error("--android-serial cannot be combined with --android-manifest")
        if not args.android_artifacts_dir:
            parser.error("--android-serial requires --android-artifacts-dir")
        target_names = packet.get("targetAllowlist", {}).get("androidTargets", [args.android_serial])
        android_manifest_value = android_manifest(adapter_id="adb-android", host_id=host_id, target_allowlist=target_names)
        android_transport = ADBAndroidTransport(
            serial=args.android_serial,
            artifacts_dir=args.android_artifacts_dir,
            adb_binary=args.adb_path,
            apk_path=args.android_apk,
            package_name=args.android_package,
            activity_name=args.android_activity,
        )
    else:
        android_manifest_value = _load(args.android_manifest) if args.android_manifest else unavailable_manifest(adapter_id="android-host-adapter", kind="android_emulator")

    ios_transport = None
    if args.ios_udid:
        if args.ios_manifest:
            parser.error("--ios-udid cannot be combined with --ios-manifest")
        if not args.ios_artifacts_dir:
            parser.error("--ios-udid requires --ios-artifacts-dir")
        target_names = packet.get("targetAllowlist", {}).get("iosTargets", [args.ios_udid])
        ios_manifest_value = ios_manifest(adapter_id="xcrun-ios-simulator", host_id=host_id, target_allowlist=target_names)
        ios_transport = IOSSimulatorTransport(
            udid=args.ios_udid,
            artifacts_dir=args.ios_artifacts_dir,
            xcrun_binary=args.xcrun_path,
            app_path=args.ios_app,
            bundle_id=args.ios_bundle_id,
        )
    else:
        ios_manifest_value = _load(args.ios_manifest) if args.ios_manifest else unavailable_manifest(adapter_id="ios-host-adapter", kind="ios_simulator")

    flutter_transport = None
    if args.flutter_device:
        if args.flutter_manifest:
            parser.error("--flutter-device cannot be combined with --flutter-manifest")
        if not args.flutter_project_root:
            parser.error("--flutter-device requires --flutter-project-root")
        if not args.flutter_artifacts_dir:
            parser.error("--flutter-device requires --flutter-artifacts-dir")
        target_names = packet.get("targetAllowlist", {}).get("flutterTargets", [args.flutter_device])
        flutter_manifest_value = flutter_manifest(adapter_id="flutter-cli", host_id=host_id, target_allowlist=target_names)
        flutter_transport = FlutterRuntimeTransport(
            project_root=args.flutter_project_root,
            artifacts_dir=args.flutter_artifacts_dir,
            device_id=args.flutter_device,
            target_platform=args.flutter_platform,
            flutter_binary=args.flutter_path,
        )
    else:
        flutter_manifest_value = _load(args.flutter_manifest) if args.flutter_manifest else unavailable_manifest(adapter_id="flutter-host-adapter", kind="flutter")

    registry = AdapterRegistry()
    registry.register(BrowserQAAdapter(browser_manifest_value, browser_transport))
    registry.register(AndroidQAAdapter(android_manifest_value, android_transport))
    registry.register(IOSSimulatorQAAdapter(ios_manifest_value, ios_transport))
    registry.register(FlutterQAAdapter(flutter_manifest_value, flutter_transport))
    result = run_qa_workflow(workflow, packet, registry)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
