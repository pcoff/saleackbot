import hmac
import hashlib
from typing import Dict, Any

class CryptoBot:
    def __init__(self, token: str):
        self.token = token
        
    def verify_webhook(self, request_data: Dict[str, Any], signature: str) -> bool:
        """Verify CryptoBot webhook signature"""
        secret_key = hashlib.sha256(self.token.encode()).digest()
        
        # Получаем строку для подписи
        check_string = '\n'.join([
            str(request_data.get('id', '')),
            str(request_data.get('status', '')),
            str(request_data.get('payload', ''))
        ])
        
        # Создаем подпись
        computed_signature = hmac.new(
            secret_key,
            check_string.encode(),
            hashlib.sha256
        ).hexdigest()
        
        return computed_signature == signature
    
    async def get_invoice_status(self, invoice_id: str) -> Dict[str, Any]:
        """Get invoice status from CryptoBot"""
        import aiohttp
        import ssl
        import certifi
        
        headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json"
        }
        
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                f"https://pay.crypt.bot/api/getInvoices",
                headers=headers,
                params={"invoice_ids": str(invoice_id)}
            ) as resp:
                return await resp.json()
    
    async def confirm_payment(self, invoice_id: str) -> Dict[str, Any]:
        """Confirm invoice payment"""
        import aiohttp
        import ssl
        import certifi
        
        headers = {
            "Crypto-Pay-API-Token": self.token,
            "Content-Type": "application/json"
        }
        
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        connector = aiohttp.TCPConnector(ssl=ssl_context)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.post(
                f"https://pay.crypt.bot/api/confirmPayment",
                headers=headers,
                json={"invoice_id": invoice_id}
            ) as resp:
                return await resp.json()