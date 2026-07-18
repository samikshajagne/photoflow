"""WS 4.3.1 tests: multi-category event classification by mood colour."""

from __future__ import annotations

from core.album.event_classifier import (
    BARAAT,
    CEREMONY,
    HALDI,
    MEHNDI,
    PORTRAITS,
    RECEPTION,
    classify_event,
    event_name,
)


def test_haldi_yellow():
    c = classify_event((230, 200, 40))  # turmeric yellow
    assert c.event_type == HALDI
    assert c.confidence >= 0.45


def test_mehndi_green():
    assert classify_event((60, 170, 70)).event_type == MEHNDI


def test_baraat_red():
    assert classify_event((200, 40, 40)).event_type == BARAAT


def test_portraits_neutral():
    assert classify_event((190, 188, 185)).event_type == PORTRAITS


def test_reception_gold_amber():
    # Warm amber/gold reads as reception.
    assert classify_event((210, 150, 40)).event_type == RECEPTION


def test_ceremony_fallback_for_odd_hue():
    # A saturated blue/purple isn't a named ceremony colour -> Ceremony fallback.
    assert classify_event((60, 60, 200)).event_type == CEREMONY


def test_event_name_suppresses_low_confidence_and_ceremony():
    assert event_name((60, 60, 200)) is None            # Ceremony -> None
    assert event_name((230, 200, 40)) == HALDI          # confident Haldi
