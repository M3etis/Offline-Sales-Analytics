import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import random
import os

def generate_sales_data(num_rows=1000):
    np.random.seed(42)
    random.seed(42)
    
    categories = ['Электроника', 'Одежда', 'Продукты', 'Мебель', 'Спорттовары']
    products_map = {
        'Электроника': ['Смартфон X', 'Ноутбук Pro', 'Планшет Z', 'Наушники Air', 'Смарт-часы'],
        'Одежда': ['Футболка базовая', 'Джинсы классика', 'Куртка зимняя', 'Кроссовки спорт', 'Свитер шерстяной'],
        'Продукты': ['Кофе зерновой', 'Чай зеленый', 'Шоколад горький', 'Сыр пармезан', 'Оливковое масло'],
        'Мебель': ['Стул офисный', 'Стол письменный', 'Диван угловой', 'Кровать двуспальная', 'Шкаф-купе'],
        'Спорттовары': ['Коврик для йоги', 'Гантели 5кг', 'Эспандер', 'Мяч фитнес', 'Скакалка']
    }
    
    prices = {
        'Электроника': (5000, 100000),
        'Одежда': (1000, 15000),
        'Продукты': (200, 2000),
        'Мебель': (3000, 50000),
        'Спорттовары': (500, 5000)
    }
    
    regions = ['Алматы', 'Астана', 'Шымкент', 'Караганда', 'Актобе']
    managers = ['Иванов И.', 'Петров П.', 'Смирнова А.', 'Козлов В.', 'Лебедева Е.']
    channels = ['Онлайн', 'Офлайн-магазин', 'Маркетплейс', 'B2B']
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365)
    
    data = []
    
    for i in range(num_rows):
        date = start_date + timedelta(days=random.randint(0, 365))
        category = random.choice(categories)
        product = random.choice(products_map[category])
        region = random.choice(regions)
        manager = random.choice(managers)
        channel = random.choice(channels)
        
        base_price = random.randint(*prices[category])
        quantity = random.randint(1, 10 if category in ['Продукты', 'Спорттовары'] else 3)
        price = base_price * (1 + np.random.normal(0, 0.1))
        
        discount_prob = random.random()
        discount = 0
        if discount_prob > 0.7:
            discount = random.choice([5, 10, 15, 20])
            
        revenue = quantity * price * (1 - discount/100)
        
        cost_margin = random.uniform(0.4, 0.8)
        cost = revenue * cost_margin
        profit = revenue - cost
        
        status = random.choices(['completed', 'returned', 'cancelled'], weights=[0.9, 0.05, 0.05])[0]
        returns = quantity if status == 'returned' else 0
        
        data.append({
            'date': date.strftime('%Y-%m-%d'),
            'order_id': f"ORD-{10000+i}",
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
            'customer_id': f"CUST-{random.randint(100, 999)}",
            'brand': f"Brand_{category[:3]}",
            'warehouse': f"Склад-{region[:3]}",
            'returns': returns,
            'status': status
        })
        
    df = pd.DataFrame(data)
    
    os.makedirs('data/demo', exist_ok=True)
    df.to_csv('data/demo/demo_sales.csv', index=False)
    print(f"Generated demo data with {num_rows} rows at data/demo/demo_sales.csv")

if __name__ == "__main__":
    generate_sales_data(2000)
