#!/usr/bin/env python3
"""
Management CLI for the crypto signal tracking platform.

Provides command-line interface for various administrative and maintenance tasks.
"""

import click

from app.cli.scoring_commands import scoring


@click.group()
def cli():
    """
    Crypto Signal Tracking Platform Management CLI.
    
    Provides commands for scoring, analytics, and system maintenance.
    """
    pass


# Add command groups
cli.add_command(scoring)


if __name__ == '__main__':
    cli()