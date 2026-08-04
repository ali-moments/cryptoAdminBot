#!/usr/bin/env python3
"""Test script for leverage calculation logic."""

import math
from decimal import Decimal


def normalize_leverage(n: int) -> int:
    """
    Normalize leverage according to the given rules:
    - ≤ 10          → 10
    - 11 … 19       → next even number (11→12, 13→14, …, 19→20)
    - ≥ 20          → ceiling to the next multiple of 5, capped at 40
    """
    if n <= 10:
        return 10

    if n <= 19:
        return n + (n % 2)  # make it even (round up when odd)

    # ceiling to nearest multiple of 5, then cap at 40
    return min(((n + 4) // 5) * 5, 40)


def calculate_leverage(entry: Decimal, stop_loss: Decimal) -> int:
    """
    Calculate leverage based on entry price and stop loss.
    Formula: leverage = normalize(ceil(80 / ((entry - sl) / entry * 100)))
    """
    # Calculate percentage distance from entry to stop loss
    distance_pct = abs((entry - stop_loss) / entry * Decimal(100))
    
    # Avoid division by zero
    if distance_pct == 0:
        return 10
    
    # Calculate raw leverage: 80 / distance_pct
    raw_leverage = Decimal(80) / distance_pct
    
    # Ceil and normalize
    leverage = math.ceil(raw_leverage)
    return normalize_leverage(leverage)


def test_normalize():
    """Test the normalize function."""
    print("Testing normalize function:")
    print(f"  normalize(5) = {normalize_leverage(5)} (expected: 10)")
    print(f"  normalize(10) = {normalize_leverage(10)} (expected: 10)")
    print(f"  normalize(11) = {normalize_leverage(11)} (expected: 12)")
    print(f"  normalize(13) = {normalize_leverage(13)} (expected: 14)")
    print(f"  normalize(19) = {normalize_leverage(19)} (expected: 20)")
    print(f"  normalize(20) = {normalize_leverage(20)} (expected: 20)")
    print(f"  normalize(21) = {normalize_leverage(21)} (expected: 25)")
    print(f"  normalize(23) = {normalize_leverage(23)} (expected: 25)")
    print(f"  normalize(28) = {normalize_leverage(28)} (expected: 30)")
    print(f"  normalize(40) = {normalize_leverage(40)} (expected: 40)")
    print(f"  normalize(50) = {normalize_leverage(50)} (expected: 40, capped)")
    print()


def test_calculate_leverage():
    """Test the calculate_leverage function."""
    print("Testing calculate_leverage function:")
    
    # LONG example: entry_high=100, sl=95
    # distance = (100-95)/100*100 = 5%
    # raw = 80/5 = 16
    # normalized = 16 (even)
    entry = Decimal("100.0")
    sl = Decimal("95.0")
    result = calculate_leverage(entry, sl)
    print(f"  LONG: entry={entry}, sl={sl} → leverage={result} (expected: 16)")
    
    # LONG example: entry_high=50000, sl=48000
    # distance = (50000-48000)/50000*100 = 4%
    # raw = 80/4 = 20
    # normalized = 20
    entry = Decimal("50000.0")
    sl = Decimal("48000.0")
    result = calculate_leverage(entry, sl)
    print(f"  LONG: entry={entry}, sl={sl} → leverage={result} (expected: 20)")
    
    # SHORT example: entry_low=100, sl=110
    # distance = |100-110|/100*100 = 10%
    # raw = 80/10 = 8
    # normalized = 10 (minimum)
    entry = Decimal("100.0")
    sl = Decimal("110.0")
    result = calculate_leverage(entry, sl)
    print(f"  SHORT: entry={entry}, sl={sl} → leverage={result} (expected: 10)")
    
    # SHORT example: entry_low=1.0, sl=1.02
    # distance = |1.0-1.02|/1.0*100 = 2%
    # raw = 80/2 = 40
    # normalized = 40
    entry = Decimal("1.0")
    sl = Decimal("1.02")
    result = calculate_leverage(entry, sl)
    print(f"  SHORT: entry={entry}, sl={sl} → leverage={result} (expected: 40)")
    
    # Edge case: very tight stop loss
    # distance = 1%
    # raw = 80/1 = 80
    # normalized = 40 (capped)
    entry = Decimal("100.0")
    sl = Decimal("99.0")
    result = calculate_leverage(entry, sl)
    print(f"  Tight SL: entry={entry}, sl={sl} → leverage={result} (expected: 40, capped)")
    
    # Edge case: 3.5% distance
    # raw = 80/3.5 = 22.857...
    # ceil = 23
    # normalized = 25
    entry = Decimal("100.0")
    sl = Decimal("96.5")
    result = calculate_leverage(entry, sl)
    print(f"  3.5% distance: entry={entry}, sl={sl} → leverage={result} (expected: 25)")
    
    print()


if __name__ == "__main__":
    test_normalize()
    test_calculate_leverage()
    print("✅ All tests completed!")
