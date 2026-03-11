import sqlite3
import os
from datetime import datetime, timedelta
import random

def create_sample_database():
    """Create a comprehensive sample database for testing"""
    
    print("🚀 Creating sample database...")
    
    # Connect to SQLite database
    conn = sqlite3.connect('sample_database.db')
    cursor = conn.cursor()
    
    # Drop existing tables if they exist
    cursor.execute("DROP TABLE IF EXISTS order_items")
    cursor.execute("DROP TABLE IF EXISTS orders")
    cursor.execute("DROP TABLE IF EXISTS products")
    cursor.execute("DROP TABLE IF EXISTS customers")
    cursor.execute("DROP TABLE IF EXISTS categories")
    
    # Create categories table
    cursor.execute('''
        CREATE TABLE categories (
            category_id INTEGER PRIMARY KEY,
            category_name TEXT NOT NULL,
            description TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create customers table
    cursor.execute('''
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            phone TEXT,
            signup_date DATE NOT NULL,
            city TEXT,
            country TEXT,
            age INTEGER,
            loyalty_points INTEGER DEFAULT 0,
            is_active BOOLEAN DEFAULT 1,
            last_login TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create products table
    cursor.execute('''
        CREATE TABLE products (
            product_id INTEGER PRIMARY KEY,
            product_name TEXT NOT NULL,
            category_id INTEGER,
            description TEXT,
            price DECIMAL(10,2) NOT NULL,
            cost DECIMAL(10,2),
            stock_quantity INTEGER DEFAULT 0,
            reorder_level INTEGER DEFAULT 10,
            supplier TEXT,
            is_discontinued BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories (category_id)
        )
    ''')
    
    # Create orders table
    cursor.execute('''
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_date TIMESTAMP NOT NULL,
            required_date DATE,
            shipped_date DATE,
            status TEXT CHECK(status IN ('Pending', 'Processing', 'Shipped', 'Delivered', 'Cancelled')),
            total_amount DECIMAL(10,2) DEFAULT 0,
            payment_method TEXT,
            shipping_address TEXT,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
        )
    ''')
    
    # Create order_items table
    cursor.execute('''
        CREATE TABLE order_items (
            order_item_id INTEGER PRIMARY KEY,
            order_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            quantity INTEGER NOT NULL,
            unit_price DECIMAL(10,2) NOT NULL,
            discount DECIMAL(4,2) DEFAULT 0,
            total_price DECIMAL(10,2) GENERATED ALWAYS AS (quantity * unit_price * (1 - discount/100)) STORED,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (order_id) REFERENCES orders (order_id),
            FOREIGN KEY (product_id) REFERENCES products (product_id)
        )
    ''')
    
    # Insert sample categories
    categories = [
        (1, 'Electronics', 'Electronic devices and accessories'),
        (2, 'Clothing', 'Apparel and fashion items'),
        (3, 'Books', 'Books and publications'),
        (4, 'Home & Garden', 'Home improvement and garden supplies'),
        (5, 'Sports', 'Sports equipment and accessories'),
        (6, 'Toys', 'Toys and games'),
        (7, 'Food', 'Food and beverages'),
        (8, 'Beauty', 'Beauty and personal care')
    ]
    cursor.executemany('INSERT INTO categories VALUES (?,?,?, CURRENT_TIMESTAMP)', categories)
    
    # Insert sample customers
    first_names = ['John', 'Jane', 'Michael', 'Sarah', 'David', 'Emma', 'James', 'Lisa', 'Robert', 'Maria']
    last_names = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez']
    cities = ['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix', 'Philadelphia', 'San Antonio', 'San Diego', 'Dallas', 'San Jose']
    countries = ['USA', 'USA', 'USA', 'USA', 'USA', 'USA', 'USA', 'USA', 'USA', 'USA']
    
    customers = []
    for i in range(1, 101):  # 100 customers
        signup_date = datetime.now() - timedelta(days=random.randint(1, 365))
        last_login = signup_date + timedelta(days=random.randint(1, 30))
        customers.append((
            i,
            random.choice(first_names),
            random.choice(last_names),
            f"customer{i}@email.com",
            f"+1-{random.randint(200,999)}-{random.randint(100,999)}-{random.randint(1000,9999)}",
            signup_date.date(),
            random.choice(cities),
            random.choice(countries),
            random.randint(18, 80),
            random.randint(0, 1000),
            random.choice([True, False]) if i > 10 else True,
            last_login
        ))
    
    cursor.executemany('''
        INSERT INTO customers 
        (customer_id, first_name, last_name, email, phone, signup_date, city, country, age, loyalty_points, is_active, last_login)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    ''', customers)
    
    # Insert sample products
    products = []
    product_names = [
        ('Smartphone', 1, 699.99, 450.00, 150),
        ('Laptop', 1, 1299.99, 900.00, 50),
        ('T-Shirt', 2, 19.99, 8.00, 500),
        ('Jeans', 2, 49.99, 20.00, 300),
        ('Python Programming Book', 3, 39.99, 15.00, 200),
        ('Garden Hose', 4, 24.99, 10.00, 150),
        ('Tennis Racket', 5, 89.99, 45.00, 75),
        ('Board Game', 6, 34.99, 15.00, 100),
        ('Coffee Beans', 7, 14.99, 6.00, 400),
        ('Shampoo', 8, 8.99, 3.00, 600)
    ]
    
    for i in range(1, 51):  # 50 products
        base_product = random.choice(product_names)
        products.append((
            i,
            f"{base_product[0]} {random.choice(['Pro', 'Basic', 'Deluxe', 'Standard', 'Premium'])}",
            base_product[1],
            f"Description for product {i}",
            base_product[2] * random.uniform(0.8, 1.2),
            base_product[3] * random.uniform(0.8, 1.2),
            random.randint(10, 500),
            10,
            random.choice(['Supplier A', 'Supplier B', 'Supplier C', 'Supplier D']),
            0
        ))
    
    cursor.executemany('''
        INSERT INTO products 
        (product_id, product_name, category_id, description, price, cost, stock_quantity, reorder_level, supplier, is_discontinued)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    ''', products)
    
    # Insert sample orders and order items
    statuses = ['Pending', 'Processing', 'Shipped', 'Delivered', 'Cancelled']
    payment_methods = ['Credit Card', 'PayPal', 'Bank Transfer', 'Cash on Delivery']
    
    for order_id in range(1, 501):  # 500 orders
        customer_id = random.randint(1, 100)
        order_date = datetime.now() - timedelta(days=random.randint(1, 180))
        required_date = order_date + timedelta(days=random.randint(3, 10))
        
        # 70% chance order is shipped
        if random.random() < 0.7:
            shipped_date = order_date + timedelta(days=random.randint(1, 3))
        else:
            shipped_date = None
        
        status = random.choices(statuses, weights=[0.1, 0.2, 0.2, 0.4, 0.1])[0]
        payment_method = random.choice(payment_methods)
        
        cursor.execute('''
            INSERT INTO orders 
            (order_id, customer_id, order_date, required_date, shipped_date, status, payment_method, shipping_address, notes)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (
            order_id, customer_id, order_date, required_date, shipped_date, 
            status, payment_method, f"Address {customer_id}", f"Notes for order {order_id}"
        ))
        
        # Add 1-5 items per order
        total_amount = 0
        for item_num in range(random.randint(1, 5)):
            product_id = random.randint(1, 50)
            quantity = random.randint(1, 3)
            
            # Get product price
            cursor.execute("SELECT price FROM products WHERE product_id = ?", (product_id,))
            price = cursor.fetchone()[0]
            
            discount = random.choice([0, 5, 10, 15, 20]) if random.random() < 0.3 else 0
            
            cursor.execute('''
                INSERT INTO order_items 
                (order_id, product_id, quantity, unit_price, discount)
                VALUES (?,?,?,?,?)
            ''', (order_id, product_id, quantity, price, discount))
            
            total_amount += quantity * price * (1 - discount/100)
        
        # Update order total
        cursor.execute("UPDATE orders SET total_amount = ? WHERE order_id = ?", (round(total_amount, 2), order_id))
    
    conn.commit()
    conn.close()
    
    print("✅ Sample database created successfully!")
    print("\n📊 Database Statistics:")
    print("-" * 30)
    print(f"👥 Customers: 100")
    print(f"📦 Products: 50")
    print(f"📋 Categories: 8")
    print(f"🛍️ Orders: 500")
    print(f"📝 Order Items: ~1500")
    
    # Update .env file
    env_content = """# Anthropic API
ANTHROPIC_API_KEY=your-api-key-here

# Database configuration
DB_TYPE=sqlite
DB_PATH=sample_database.db
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("\n✅ .env file created/updated")
    print("⚠️  Don't forget to add your Anthropic API key to the .env file!")

if __name__ == "__main__":
    create_sample_database()