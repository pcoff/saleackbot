#!/usr/bin/env python3
"""
Тестовый скрипт для проверки работы системы очереди покупок.
"""

import os
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database.database import Database

def test_queue_system():
    # Создаем тестовую базу данных
    db = Database('test_queue.db')
    
    print("🧪 Тестирование системы очереди...")
    
    # 1. Создаем тестовый лот
    account_id = db.add_account("Test VEO3 Account", 50.0)
    print(f"✅ Создан тестовый лот #{account_id}")
    
    # 2. Проверяем что лот пустой
    available = db.count_available_credentials(account_id)
    print(f"📦 Доступных аккаунтов в лоте: {available}")
    
    # 3. Добавляем пользователей в очередь
    queue_id1 = db.add_to_purchase_queue(
        user_id=12345,
        account_id=account_id,
        payment_type="crypto",
        price_usdt=50.0,
        username="user1",
        invoice_id="inv123",
        payment_status="paid"
    )
    print(f"👤 Добавлен пользователь 1 в очередь (ID: {queue_id1})")
    
    queue_id2 = db.add_to_purchase_queue(
        user_id=67890,
        account_id=account_id,
        payment_type="rub",
        price_usdt=50.0,
        price_rub=4750,
        username="user2",
        payment_status="paid"
    )
    print(f"👤 Добавлен пользователь 2 в очередь (ID: {queue_id2})")
    
    # 4. Проверяем размер очереди
    queue_size = db.get_queue_size(account_id)
    print(f"👥 Размер очереди: {queue_size}")
    
    # 5. Добавляем логи и проверяем обработку очереди
    print("\n📋 Добавляем логи в лот...")
    db.add_credential(account_id, "login1:password1:email1@test.com")
    db.add_credential(account_id, "login2:password2:email2@test.com")
    
    available_after = db.count_available_credentials(account_id)
    print(f"📦 Доступных аккаунтов после добавления: {available_after}")
    
    # 6. Обрабатываем очередь
    queue_entries = db.process_queue_for_lot(account_id)
    print(f"🔄 Найдено записей в очереди для обработки: {len(queue_entries)}")
    
    for entry in queue_entries:
        queue_id, user_id, payment_type, price_usdt, price_rub, username, invoice_id, payment_status = entry
        print(f"   - Пользователь {user_id} (@{username}), тип: {payment_type}, статус: {payment_status}")
    
    # 7. Симулируем продажу
    print("\n💰 Симулируем продажи...")
    success1, details1, depleted1 = db.mark_account_sold(account_id, 12345, 50.0)
    if success1:
        print(f"✅ Продажа пользователю 12345: {details1}")
        db.mark_queue_entry_fulfilled(queue_id1)
        print("✅ Запись в очереди помечена как выполненная")
    
    success2, details2, depleted2 = db.mark_account_sold(account_id, 67890, 50.0)
    if success2:
        print(f"✅ Продажа пользователю 67890: {details2}")
        db.mark_queue_entry_fulfilled(queue_id2)
        print("✅ Запись в очереди помечена как выполненная")
    
    # 8. Проверяем финальное состояние
    final_available = db.count_available_credentials(account_id)
    final_queue_size = db.get_queue_size(account_id)
    print(f"\n📊 Финальное состояние:")
    print(f"   - Доступных аккаунтов: {final_available}")
    print(f"   - Размер очереди: {final_queue_size}")
    print(f"   - Лот истощен: {depleted2}")
    
    # 9. Добавляем еще одного пользователя в очередь для истощенного лота
    queue_id3 = db.add_to_purchase_queue(
        user_id=11111,
        account_id=account_id,
        payment_type="crypto",
        price_usdt=50.0,
        username="user3",
        invoice_id="inv456",
        payment_status="paid"
    )
    
    final_queue_size2 = db.get_queue_size(account_id)
    print(f"\n🔄 После добавления пользователя в очередь для истощенного лота:")
    print(f"   - Размер очереди: {final_queue_size2}")
    
    print("\n✅ Тест системы очереди завершен!")
    
    # Удаляем тестовую базу
    import os
    os.remove('test_queue.db')
    print("🗑️ Тестовая база данных удалена")

if __name__ == "__main__":
    test_queue_system()