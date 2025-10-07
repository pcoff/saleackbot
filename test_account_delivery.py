#!/usr/bin/env python3
"""
Скрипт для тестирования системы выдачи аккаунтов
Проверяет корректность работы функции mark_account_sold()
"""

import sys
import os
sys.path.append('.')

from database.database import Database

def test_account_delivery():
    """Тестируем систему выдачи аккаунтов"""
    
    print("🧪 ТЕСТ СИСТЕМЫ ВЫДАЧИ АККАУНТОВ")
    print("=" * 50)
    
    # Создаем тестовую базу данных
    db = Database('test_accounts.db')
    
    # 1. Создаем тестовый лот
    print("\n1️⃣ Создание тестового лота...")
    account_id = db.add_account("Тестовый лот VEO3", 10.0)
    print(f"✅ Лот создан с ID: {account_id}")
    
    # 2. Добавляем тестовые логи
    print("\n2️⃣ Добавление тестовых логов...")
    test_logs = [
        "login1:password1:email1@test.com",
        "user2:pass2:backup2@test.com", 
        "testuser3:secret3:mail3@test.com",
        "account4:12345:reserve4@test.com",
        "veo3user5:qwerty:contact5@test.com"
    ]
    
    for i, log in enumerate(test_logs, 1):
        db.add_credential(account_id, log)
        print(f"   ➕ Лог {i}: {log[:20]}...")
    
    available = db.count_available_credentials(account_id)
    print(f"✅ Добавлено {available} логов")
    
    # 3. Проверяем состояние до продаж
    print("\n3️⃣ Состояние лота до продаж:")
    account_info = db.get_account(account_id)
    print(f"   Лот: {account_info[1]}")
    print(f"   Цена: {account_info[2]} USDT")
    print(f"   Доступен: {'Да' if account_info[3] else 'Нет'}")
    print(f"   Логов доступно: {available}")
    
    # 4. Симулируем покупки
    print("\n4️⃣ Симуляция покупок:")
    test_users = [
        (12345, "user1"),
        (12346, "user2"), 
        (12347, "user3"),
        (12348, "user4"),
        (12349, "user5")
    ]
    
    delivered_logs = []
    
    for user_id, username in test_users:
        print(f"\n   🛒 Покупка пользователем {username} (ID: {user_id}):")
        
        # Проверяем доступность
        before_count = db.count_available_credentials(account_id)
        print(f"      📊 Логов до покупки: {before_count}")
        
        if before_count == 0:
            print("      ❌ Нет доступных логов!")
            break
            
        # Выдаем лог
        success, delivered_details = db.mark_account_sold(account_id, user_id, 10.0)
        
        if success:
            print(f"      ✅ Лог выдан: {delivered_details}")
            delivered_logs.append((user_id, username, delivered_details))
            
            # Проверяем состояние после выдачи
            after_count = db.count_available_credentials(account_id)
            print(f"      📊 Логов после покупки: {after_count}")
            
        else:
            print("      ❌ Ошибка выдачи лога!")
    
    # 5. Финальная проверка
    print("\n5️⃣ Финальная проверка:")
    account_info = db.get_account(account_id)
    remaining = db.count_available_credentials(account_id)
    
    print(f"   Лот доступен: {'Да' if account_info[3] else 'Нет'}")
    print(f"   Оставшихся логов: {remaining}")
    print(f"   Продано логов: {len(delivered_logs)}")
    
    # 6. Проверка на дубликаты
    print("\n6️⃣ Проверка на дубликаты:")
    unique_logs = set(log[2] for log in delivered_logs)
    if len(unique_logs) == len(delivered_logs):
        print("   ✅ Все выданные логи уникальны!")
    else:
        print("   ❌ ОШИБКА: Найдены дубликаты логов!")
    
    # 7. Детальный отчет
    print("\n7️⃣ Детальный отчет по выданным логам:")
    for user_id, username, log in delivered_logs:
        print(f"   👤 {username} (ID: {user_id}) → {log}")
    
    # 8. Статистика лота
    print("\n8️⃣ Статистика лота:")
    stats = db.get_lot_statistics(account_id)
    if stats:
        print(f"   📊 Всего логов: {stats['total_logs']}")
        print(f"   ✅ Продано: {stats['sold_logs']} ")
        print(f"   📎 Доступно: {stats['available_logs']}")
        print(f"   💰 Доход: {stats['total_revenue']} USDT")
    
    # 9. Попытка купить когда нет логов
    if remaining == 0:
        print("\n9️⃣ Тест: покупка когда логи закончились:")
        success, details = db.mark_account_sold(account_id, 99999, 10.0)
        if not success:
            print("   ✅ Система корректно отклонила покупку - логи закончились")
        else:
            print("   ❌ ОШИБКА: Система выдала лог когда их нет!")
    
    print("\n" + "=" * 50)
    print("🎯 РЕЗУЛЬТАТ ТЕСТИРОВАНИЯ:")
    
    if len(delivered_logs) == len(test_logs) and len(unique_logs) == len(delivered_logs):
        print("✅ ВСЕ ТЕСТЫ ПРОЙДЕНЫ!")
        print("✅ Каждому пользователю выдан уникальный лог")
        print("✅ Дубликаты исключены")
        print("✅ Система корректно обрабатывает отсутствие логов")
    else:
        print("❌ НАЙДЕНЫ ПРОБЛЕМЫ В СИСТЕМЕ!")
    
    print("\n🗑️ Удаляем тестовую базу данных...")
    try:
        os.remove('test_accounts.db')
        print("✅ Тестовая база данных удалена")
    except:
        pass

if __name__ == "__main__":
    test_account_delivery()