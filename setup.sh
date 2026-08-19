#!/bin/bash

set -euo pipefail

# Colors for outputt
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if .env exists
check_env_file() {
    if [[ ! -f ".env" ]]; then
        log_error ".env file not found!"
        log_error "Please create a .env file with your configuration."
        log_error "You can use the existing .env as a reference."
        exit 1
    fi
    log_success ".env file found"
}

# Check if required tools are available
check_dependencies() {
    log_info "Checking dependencies..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker is not installed or not in PATH"
        log_error "Please install Docker first: https://docs.docker.com/get-docker/"
        exit 1
    fi

    if ! command -v docker-compose &> /dev/null && ! docker compose version &> /dev/null; then
        log_error "Docker Compose is not available"
        log_error "Please install Docker Compose: https://docs.docker.com/compose/install/"
        exit 1
    fi

    log_success "Dependencies check passed"
}

# Wait for PostgreSQL to be healthy
wait_for_postgres() {
    log_info "Waiting for PostgreSQL to be healthy..."

    local max_attempts=30
    local attempt=1

    while [[ $attempt -le $max_attempts ]]; do
        if docker compose ps postgres | grep -q "healthy"; then
            log_success "PostgreSQL is healthy"
            return 0
        fi

        log_info "Attempt $attempt/$max_attempts: PostgreSQL not ready yet, waiting..."
        sleep 2
        ((attempt++))
    done

    log_error "PostgreSQL failed to become healthy after $max_attempts attempts"
    log_error "Check PostgreSQL logs with: docker compose logs postgres"
    exit 1
}

# Run database migrations
run_migrations() {
    log_info "Running database migrations..."

    if docker compose run --rm bot uv run alembic upgrade head; then
        log_success "Database migrations completed successfully"
    else
        log_error "Database migrations failed"
        exit 1
    fi
}

# Run database seed
run_seed() {
    log_info "Running database seed..."

    if docker compose run --rm bot uv run scripts/seed.py; then
        log_success "Database seed completed successfully"
    else
        log_error "Database seed failed"
        exit 1
    fi
}

# Run QR login
run_qr_login() {
    log_info "Starting Telegram QR login process..."
    log_warning "You will need to scan QR codes for both reader and sender sessions"

    if docker compose run --rm -it bot uv run scripts/qr_login.py; then
        log_success "Telegram QR login completed successfully"
    else
        log_error "Telegram QR login failed"
        exit 1
    fi
}

# Fresh installation
fresh_install() {
    log_info "Starting fresh installation..."

    check_env_file
    check_dependencies

    log_info "Building and starting PostgreSQL and Adminer..."
    docker compose up -d postgres adminer

    wait_for_postgres

    run_migrations
    run_seed

    # Create required directories if they don't exist
    mkdir -p sessions generated logs

    run_qr_login

    log_info "Starting the bot..."
    docker compose up -d bot

    log_success "Fresh installation completed successfully!"
    log_info "Services status:"
    docker compose ps

    log_info ""
    log_success "🎉 Your crypto trading bot is now running!"
    log_info "📊 Adminer (Database UI): http://localhost:8080"
    log_info "📋 Check logs with: docker compose logs -f bot"
    log_info "🔄 Restart bot with: docker compose restart bot"
    log_info "🛑 Stop all services with: docker compose down"
}

# Upgrade existing installation
upgrade() {
    log_info "Starting upgrade process..."

    check_env_file
    check_dependencies

    log_info "Pulling latest changes from Git..."
    if git pull; then
        log_success "Git pull completed successfully"
    else
        log_warning "Git pull failed or no git repository found. Continuing with existing code..."
    fi

    log_info "Rebuilding bot image..."
    docker compose build bot

    log_info "Ensuring PostgreSQL is healthy..."
    if ! docker compose ps postgres | grep -q "healthy"; then
        log_info "PostgreSQL not running or not healthy. Starting it..."
        docker compose up -d postgres
        wait_for_postgres
    else
        log_success "PostgreSQL is already healthy"
    fi

    run_migrations

    log_info "Restarting bot with new image..."
    docker compose up -d bot

    log_success "Upgrade completed successfully!"
    log_info "Services status:"
    docker compose ps

    log_info ""
    log_success "🚀 Your crypto trading bot has been upgraded!"
    log_info "📋 Check logs with: docker compose logs -f bot"
}

# Show usage information
show_usage() {
    echo "Usage: $0 [upgrade]"
    echo ""
    echo "Commands:"
    echo "  (no args)  - Fresh installation"
    echo "  upgrade    - Upgrade existing installation"
    echo ""
    echo "Examples:"
    echo "  bash setup.sh         # Fresh install"
    echo "  bash setup.sh upgrade # Upgrade existing"
}

# Main script logic
main() {
    log_info "Crypto Trading Bot Setup Script"
    log_info "==============================="

    if [[ $# -eq 0 ]]; then
        fresh_install
    elif [[ $# -eq 1 ]] && [[ "$1" == "upgrade" ]]; then
        upgrade
    else
        log_error "Invalid arguments"
        show_usage
        exit 1
    fi
}

# Handle script interruption
trap 'log_error "Script interrupted"; exit 1' INT TERM

# Run main function with all arguments
main "$@"
