#!/usr/bin/env python3
"""
Market module test runner.

Usage:
    python run_market_tests.py [category]
    
Categories:
    health     - Core health system tests (fast, no network)
    performance - Performance and benchmark tests  
    integration - Real market data integration tests
    stress     - Stress and load tests
    production - Production scenario tests
    all        - Run all tests
"""

import sys
import subprocess
import time
from pathlib import Path

def run_tests(category: str):
    """Run tests for specified category"""
    
    test_commands = {
        'health': [
            'python', '-m', 'pytest', 
            'tests/market/test_health_system.py',
            '-v', '-s', '--tb=short'
        ],
        'performance': [
            'python', '-m', 'pytest',
            'tests/market/test_market_performance.py', 
            '-v', '-s', '--tb=short'
        ],
        'integration': [
            'python', '-m', 'pytest',
            'tests/market/test_market_integration.py::TestMarketIntegration',
            '-v', '-s', '--tb=short'
        ],
        'stress': [
            'python', '-m', 'pytest',
            'tests/market/test_market_integration.py::TestMarketStressTests',
            '-v', '-s', '--tb=short'
        ],
        'production': [
            'python', '-m', 'pytest',
            'tests/market/test_market_integration.py::TestProductionScenarios',
            '-v', '-s', '--tb=short'
        ],
        'per_symbol': [
            'python', '-m', 'pytest',
            'tests/market/test_per_symbol_routing.py',
            '-v', '-s', '--tb=short'
        ]
    }
    
    if category == 'all':
        categories = ['health', 'performance', 'per_symbol', 'integration']
        for cat in categories:
            print(f"\n{'='*60}")
            print(f"🧪 RUNNING {cat.upper()} TESTS")
            print(f"{'='*60}")
            
            start_time = time.time()
            result = subprocess.run(test_commands[cat])
            duration = time.time() - start_time
            
            if result.returncode == 0:
                print(f"✅ {cat.upper()} TESTS PASSED ({duration:.1f}s)")
            else:
                print(f"❌ {cat.upper()} TESTS FAILED ({duration:.1f}s)")
                
        return
    
    if category not in test_commands:
        print(f"❌ Unknown category: {category}")
        print(f"Available categories: {list(test_commands.keys())}")
        return
        
    print(f"🧪 Running {category} tests...")
    start_time = time.time()
    
    result = subprocess.run(test_commands[category])
    
    duration = time.time() - start_time
    
    if result.returncode == 0:
        print(f"\n✅ {category.upper()} TESTS PASSED ({duration:.1f}s)")
    else:
        print(f"\n❌ {category.upper()} TESTS FAILED ({duration:.1f}s)")

def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return
        
    category = sys.argv[1].lower()
    
    # Change to project root
    project_root = Path(__file__).parent.parent.parent
    import os
    os.chdir(project_root)
    
    run_tests(category)

if __name__ == "__main__":
    main()