# Account Shop Bot

This is a Telegram bot for selling accounts with administrative features.

## Features

- View available accounts
- Purchase accounts
- Admin panel for managing accounts
  - Add new accounts
  - Update prices
  - Delete accounts
- Integration with payment options (direct contact or CryptoBot)

## Setup

1. Install dependencies:
```bash
pip install -r requirements.txt
```

2. Configure environment variables in `.env`:
```
BOT_TOKEN=your_bot_token
ADMIN_USERNAME=your_admin_username
CRYPTO_BOT_TOKEN=your_crypto_bot_token  # Optional
```

3. Run the bot:
```bash
python bot.py
```

## Commands

### User Commands
- `/start` - Start the bot
- `/help` - Show available commands
- `/accounts` - View available accounts

### Admin Commands
- `/add_account [details] [price]` - Add new account
- `/update_price [account_id] [new_price]` - Update account price
- `/delete_account [account_id]` - Delete account
- `/accounts` - View all accounts

## Payment Process

1. User selects an account to purchase
2. Bot provides payment instructions
3. User contacts admin (@eqtexw) for payment
4. After payment confirmation, admin provides account details

Optional: Users can also pay using @CryptoBot