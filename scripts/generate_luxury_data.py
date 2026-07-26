import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import os

def generate_luxury_data(num_rows=2000):
    np.random.seed(101)
    random.seed(101)
    
    categories = ['Ювелирные изделия', 'Брендовые вещи', 'Парфюмерия', 'Аксессуары']
    products_map = {
        'Ювелирные изделия': ['Кольцо с бриллиантом', 'Золотая цепь', 'Серьги с изумрудом', 'Платиновый браслет', 'Колье из жемчуга'],
        'Брендовые вещи': ['Сумка Birkin', 'Платье Haute Couture', 'Пиджак кроя Slim', 'Туфли на шпильке', 'Шелковый платок'],
        'Парфюмерия': ['Chanel No 5', 'Baccarat Rouge 540', 'Tom Ford Oud Wood', 'Dior Sauvage', 'Jo Malone Wood Sage'],
        'Аксессуары': ['Часы Rolex', 'Очки Ray-Ban', 'Кожаный ремень', 'Клатч вечерний', 'Запонки золотые']
    }
    
    prices = {
        'Ювелирные изделия': (200000, 5000000),
        'Брендовые вещи': (150000, 2000000),
        'Парфюмерия': (50000, 250000),
        'Аксессуары': (80000, 1500000)
    }
    
    regions = ['Алматы', 'Астана', 'Шымкент', 'Атырау', 'Актау']
    managers = ['Ким А.', 'Ахметова Д.', 'Омаров Т.', 'Сатпаева А.']
    channels = ['Бутик', 'VIP-клиенты', 'Онлайн-магазин']
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    data = []
    
    for i in range(num_rows):
        date = start_date + timedelta(days=random.randint(0, 365))
        category = random.choice(categories)
        product = random.choice(products_map[category])
        region = random.choice(regions)
        manager = random.choice(managers)
        channel = random.choices(channels, weights=[0.6, 0.3, 0.1])[0]
        
        base_price = random.randint(*prices[category])
        quantity = random.choices([1, 2, 3], weights=[0.85, 0.1, 0.05])[0]
        price = base_price * (1 + np.random.normal(0, 0.05))
        
        discount_prob = random.random()
        discount = 0
        if discount_prob > 0.85: # Lux rarely has discounts
            discount = random.choice([5, 10, 15])
            
        revenue = quantity * price * (1 - discount/100)
        
        cost_margin = random.uniform(0.2, 0.4) # High margin for luxury
        cost = revenue * cost_margin
        profit = revenue - cost
        
        status = random.choices(['completed', 'returned', 'cancelled'], weights=[0.95, 0.03, 0.02])[0]
        returns = quantity if status == 'returned' else 0
        
        data.append({
            'date': date.strftime('%Y-%m-%d'),
            'order_id': f"LUX-{10000+i}",
            'product': product,
            'category': category,
            'region': region,
            'manager': manager,
            'channel': channel,
            'quantity': quantity,
            'price': round(price, 2),
            'discount': discount,
            'revenue': round(revenue, 2),
            'cost': round(cost, 2),
            'profit': round(profit, 2),
            'customer_id': f"VIP-{random.randint(10, 999)}",
            'brand': f"Brand_{category[:3]}",
            'warehouse': f"Склад-{region[:3]}",
            'returns': returns,
            'status': status
        })
        
    df = pd.DataFrame(data)
    
    os.makedirs('data/demo', exist_ok=True)
    df.to_csv('data/demo/demo_luxury.csv', index=False)
    print(f"Generated luxury demo data with {num_rows} rows at data/demo/demo_luxury.csv")

if __name__ == "__main__":
    generate_luxury_data(2000)
