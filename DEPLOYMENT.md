# Production Deployment Guide

This guide explains how to deploy the Crypto Trading Bot in production using Docker.

## Prerequisites

- Docker and Docker Compose installed
- Git (for upgrades)
- `.env` file configured (see `.env.example`)

## Quick Start

### Fresh Installation

1. **Configure Environment**
   ```bash
   cp .env.example .env
   # Edit .env with your actual values
   ```

2. **Run Setup**
   ```bash
   bash setup.sh
   ```

   This will:
   - ✅ Validate your `.env` file
   - 🐳 Build and start PostgreSQL + Adminer
   - ⏳ Wait for PostgreSQL to be healthy
   - 🗄️ Run database migrations
   - 🌱 Seed initial signal sources
   - 📱 Guide you through Telegram QR login
   - 🚀 Start the trading bot

### Upgrading

```bash
bash setup.sh upgrade
```

This will:
- 📥 Pull latest code from Git
- 🔨 Rebuild only the bot image
- 💾 Preserve all database data
- 🗄️ Run new migrations if any
- 🚀 Restart the bot with new code

## Services

After deployment, these services will be running:

| Service | Container | Port | Description |
|---------|-----------|------|-------------|
| PostgreSQL | `tradebot_postgres_prod` | 5432 | Database |
| Adminer | `tradebot_adminer_prod` | 8080 | Database UI |
| Trading Bot | `tradebot_bot_prod` | - | Main application |

## Data Persistence

- **Database**: Stored in Docker volume `postgres_data_prod`
- **Telegram Sessions**: Stored in `./sessions/` directory
- **Generated Files**: Stored in `./generated/` directory

## Management Commands

### View Status
```bash
docker compose ps
```

### View Logs
```bash
# All services
docker compose logs -f

# Bot only
docker compose logs -f bot

# PostgreSQL only
docker compose logs -f postgres
```

### Restart Services
```bash
# Restart bot only
docker compose restart bot

# Restart all services
docker compose restart
```

### Stop Services
```bash
# Stop all services (keeps data)
docker compose down

# Stop and remove volumes (⚠️ DELETES DATABASE)
docker compose down -v
```

### Database Management
```bash
# Access database directly
docker compose exec postgres psql -U tradebot -d tradebot

# Run migrations manually
docker compose run --rm bot uv run alembic upgrade head

# Re-seed database
docker compose run --rm bot uv run scripts/seed.py
```

### Re-run QR Login
```bash
docker compose run --rm -it bot uv run scripts/qr_login.py
```

## Environment Variables

### Required Variables

| Variable | Description | Example |
|----------|-------------|---------|
| `POSTGRES_*` | Database connection | See `.env.example` |
| `SENDER_API_ID/HASH` | Telegram sender credentials | From my.telegram.org |
| `READER_API_ID/HASH` | Telegram reader credentials | From my.telegram.org |
| `ADMIN_BOT_TOKEN` | Telegram bot token | From @BotFather |
| `*_CHANNEL` | Telegram channel IDs | Numeric IDs |
| `ADMINS` | Admin user IDs | Comma-separated |

### WebSocket URLs
The bot connects to these cryptocurrency exchanges:
- **Binance**: `wss://fstream.binance.com/ws`
- **Bybit**: `wss://stream.bybit.com/v5/public/linear`
- **OKX**: `wss://ws.okx.com:8443/ws/v5/public`

## Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Telegram      │    │   Trading Bot    │    │   PostgreSQL    │
│   Channels      │───▶│   Container      │───▶│   Container     │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                                │
                                ▼
                        ┌──────────────────┐
                        │   Exchange APIs  │
                        │  (WebSockets)    │
                        └──────────────────┘
```

## Troubleshooting

### Bot Won't Start
1. Check PostgreSQL health: `docker compose ps postgres`
2. View bot logs: `docker compose logs bot`
3. Verify `.env` file configuration

### Database Connection Issues
1. Ensure PostgreSQL is healthy: `docker compose ps postgres`
2. Check database logs: `docker compose logs postgres`
3. Verify `POSTGRES_*` variables in `.env`

### Telegram Connection Issues
1. Verify API credentials in `.env`
2. Re-run QR login: `docker compose run --rm -it bot uv run scripts/qr_login.py`
3. Check that session files exist in `./sessions/`

### Market Data Issues
1. Check WebSocket URLs in `.env`
2. View bot logs for connection errors
3. Ensure internet connectivity from container

## Security Considerations

- **Environment Variables**: Never commit `.env` to version control
- **Telegram Sessions**: Keep `./sessions/` directory secure
- **Database**: Use strong passwords for PostgreSQL
- **Network**: Consider using Docker networks for isolation
- **Backups**: Regularly backup the PostgreSQL volume

## Backup & Recovery

### Backup Database
```bash
docker compose exec postgres pg_dump -U tradebot tradebot > backup.sql
```

### Restore Database
```bash
docker compose exec -T postgres psql -U tradebot tradebot < backup.sql
```

### Backup Sessions
```bash
cp -r sessions/ sessions_backup/
```

## Production Checklist

- [ ] `.env` file configured with production values
- [ ] Strong PostgreSQL password set
- [ ] Telegram credentials configured
- [ ] All required channels and admin IDs set
- [ ] Bot successfully logged into Telegram
- [ ] Database migrations applied
- [ ] Signal sources seeded
- [ ] Logs are clean and error-free
- [ ] All services showing as healthy

## Support

For issues:
1. Check logs first: `docker compose logs -f`
2. Verify configuration in `.env`
3. Ensure all services are healthy: `docker compose ps`
4. Review this deployment guide

---

**⚠️ Important**: Always test in a development environment before deploying to production!