from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
import os
import pandas as pd
import uuid
from datetime import datetime

app = Flask(__name__)
app.secret_key = 'your_secret_key_here'

# File paths
BASE_DIR = os.path.join(os.getcwd(), 'static')
USER_FILE = os.path.join(BASE_DIR, 'users.xlsx')
INVENTORY_LOGS_FILE = os.path.join(BASE_DIR, 'inventory_logs.xlsx')
INVENTORY_FILE = os.path.join(BASE_DIR, 'inventory.xlsx')
ORDER_FILE = os.path.join(os.getcwd(), 'static', 'food system.xlsx')

# Load users with admin flag dynamically
def load_users():
    if os.path.exists(USER_FILE):
        try:
            df = pd.read_excel(USER_FILE, engine='openpyxl')
            if {'username', 'password', 'admin'}.issubset(df.columns):
                return {
                    row['username']: {
                        'password': row['password'],
                        'admin': bool(row['admin'])
                    }
                    for _, row in df.iterrows()
                }
            else:
                print("Excel file missing required columns")
        except Exception as e:
            print(f"Error reading Excel file: {e}")
    return {}
users = load_users()

def load_orders():
    if os.path.exists(ORDER_FILE):
        # Load Excel
        df = pd.read_excel(ORDER_FILE, engine="openpyxl")

        # Remove duplicate column names (e.g., multiple 'Order ID')
        df.columns = _deduplicate_columns(df.columns)

        # Manually consolidate duplicate 'Order ID'-like columns
        order_id_cols = [col for col in df.columns if 'order id' in col.lower().replace(".", "").strip()]
        if order_id_cols:
            df['Order ID'] = df[order_id_cols[0]]
            for col in order_id_cols[1:]:
                df['Order ID'] = df['Order ID'].combine_first(df[col])
            df.drop(columns=[col for col in order_id_cols if col != 'Order ID'], inplace=True)

        # Ensure required columns exist
        required_columns = ['Item Name', 'Price', 'Quantity', 'Total Price', 'User', 'Order ID', 'Timestamp', 'Status']
        for col in required_columns:
            if col not in df.columns:
                df[col] = None

        # Normalize numeric values
        df['Price'] = pd.to_numeric(df['Price'], errors='coerce').fillna(0)
        df['Quantity'] = pd.to_numeric(df['Quantity'], errors='coerce').fillna(0)
        df['Total Price'] = df['Price'] * df['Quantity']
        df['User'] = df['User'].fillna('Unknown')

        return df

    return pd.DataFrame(columns=[ 
        'Item Name', 'Price', 'Quantity', 'Total Price', 
        'User', 'Order ID', 'Timestamp', 'Status'
    ])

def _deduplicate_columns(columns):
    seen = {}
    new_cols = []
    for col in columns:
        if col in seen:
            seen[col] += 1
            new_cols.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            new_cols.append(col)
    return new_cols

@app.route('/')
def home():
    if 'username' in session:
        return render_template('home.html', username=session['username'], is_admin=session.get('is_admin', False))
    return redirect(url_for('login'))


@app.route('/inventory_logs')
def inventory_logs():
    if os.path.exists(INVENTORY_LOGS_FILE):
        df = pd.read_excel(INVENTORY_LOGS_FILE)
    else:
        df = pd.DataFrame()
    return render_template('inventory_logs.html', logs=df)

@app.route('/admin/admin_orders', methods=["GET", "POST"])
def admin_orders():
    orders_df = load_orders()  # Assuming load_orders() returns the order DataFrame
    return render_template('admin_orders.html', orders=orders_df)

@app.route('/admin/finish_order', methods=["POST"])
def finish_order():
    username = request.form.get("user")
    order_id = request.form.get("order_id")
    status_code = request.form.get("status")

    print(f"Received user: {username}, order_id: {order_id}, status: {status_code}")  # Debugging

    if not username or not order_id or status_code is None:
        return redirect(url_for("admin_orders", message="Missing order details. Please try again."))

    try:
        status_code = int(status_code)
    except ValueError:
        return redirect(url_for("admin_orders", message="Invalid status code. Please select a valid status."))

    # Load order data
    orders_df = pd.read_excel(ORDER_FILE)

    # Add 'Deducted' column if not present
    if "Deducted" not in orders_df.columns:
        orders_df["Deducted"] = False

    # Filter the specific order
    mask = (orders_df["User"] == username) & (orders_df["Order ID"].astype(str) == str(order_id))

    if not mask.any():
        return redirect(url_for("admin_orders", message="Order not found."))

    # Update the status
    orders_df.loc[mask, "Status"] = status_code

    # If status is "Completed" (3), handle inventory deduction
    if status_code == 3:
        # Load inventory
        inventory_df = pd.read_excel(INVENTORY_FILE)

        # Load or create inventory log
        try:
            inventory_log_df = pd.read_excel(INVENTORY_LOGS_FILE)
        except FileNotFoundError:
            inventory_log_df = pd.DataFrame(columns=[
                "Timestamp", "User", "Order ID", "Item", "Quantity Deducted", "Remaining Stock"
            ])

        for idx, row in orders_df[mask].iterrows():
            if orders_df.at[idx, "Deducted"] == True:
                continue  # Skip if already deducted

            item_name = row["Item Name"]
            quantity_ordered = int(row["Quantity"])
            timestamp = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")

            # Deduct from inventory
            inventory_mask = inventory_df["Item Name"] == item_name
            if inventory_mask.any():
                current_qty = int(inventory_df.loc[inventory_mask, "Quantity"].values[0])
                new_qty = max(0, current_qty - quantity_ordered)
                inventory_df.loc[inventory_mask, "Quantity"] = new_qty

                # Add to inventory log
                log_entry = {
                    "Timestamp": timestamp,
                    "User": username,
                    "Order ID": order_id,
                    "Item": item_name,
                    "Quantity Deducted": quantity_ordered,
                    "Remaining Stock": new_qty
                }
                inventory_log_df = pd.concat([inventory_log_df, pd.DataFrame([log_entry])], ignore_index=True)

                # Mark order as deducted
                orders_df.at[idx, "Deducted"] = True

    else:
        # For statuses other than 3, reset deduction-related fields
        orders_df.loc[mask, "Quantity Deducted"] = "---"
        orders_df.loc[mask, "Remaining Stock"] = "---"

    # Save all data
    orders_df.to_excel(ORDER_FILE, index=False)
    if status_code == 3:
        inventory_df.to_excel(INVENTORY_FILE, index=False)
        inventory_log_df.to_excel(INVENTORY_LOGS_FILE, index=False)

    return redirect(url_for("admin_orders", message="Order status updated successfully!"))

@app.route('/checkout', methods=['POST'])
def checkout():
    data = request.get_json()
    cart = data.get('cart', [])
    user = data.get('user', 'Anonymous')
    order_id = data.get('order_id')  # Get order_id passed from frontend
    status = data.get('status', 'Waiting for payment')

    if not cart:
        return jsonify({'message': 'Cart is empty.'}), 400

    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # Validate the order_id if needed (e.g., should it be a valid UUID or custom format?)
    if not order_id or len(order_id) != 8:
        return jsonify({'message': 'Invalid order ID.'}), 400

    # Process each item in the cart
    for item in cart:
        item['User'] = user
        item['Order ID'] = order_id
        item['Timestamp'] = timestamp
        item['Status'] = status

        # Ensure 'Price' is a float
        try:
            price = item.get('Price', 0)
            item['Price'] = float(str(price).replace('$', '').strip())
        except ValueError:
            return jsonify({'message': f"Invalid price for item {item.get('Item Name', 'Unknown')}. Cannot convert to float."}), 400

        # Calculate total price
        item['Total Price'] = item['Price'] * item.get('Quantity', 1)

    # Convert cart data to DataFrame
    df = pd.DataFrame(cart)

    # Save the order data to the Excel file
    if os.path.exists(ORDER_FILE):
        existing_df = pd.read_excel(ORDER_FILE)
        df = pd.concat([existing_df, df], ignore_index=True)
    else:
        df.to_excel(ORDER_FILE, index=False)

    df.to_excel(ORDER_FILE, index=False)

    return jsonify({'message': f'Order {order_id} submitted successfully!'}), 200

@app.route('/complete_order', methods=['POST'])
def complete_order():
    user = request.form['user']
    order_id = request.form['order_id']

    # Load current orders and inventory
    orders_df = pd.read_excel(ORDER_FILE)
    inventory_df = pd.read_excel(INVENTORY_FILE)  # Read inventory from Excel file
    print(inventory_df.columns) 
    # Filter the specific user and order ID
    user_order = orders_df[(orders_df['User'] == user) & (orders_df['Order ID'] == order_id)]

    # Update inventory quantities
    for _, row in user_order.iterrows():
        item = row['Item Name']
        qty = row['Quantity']
        inventory_df.loc[inventory_df['Item Name'] == item, 'Quantity'] -= qty

    # Load or initialize order_logs
    if os.path.exists(INVENTORY_LOGS_FILE):
        order_logs_df = pd.read_excel(INVENTORY_LOGS_FILE)
    else:
        order_logs_df = pd.DataFrame(columns=orders_df.columns)

    # Append to logs
    order_logs_df = pd.concat([order_logs_df, user_order], ignore_index=True)

    # Remove order from current list
    orders_df = orders_df[~((orders_df['User'] == user) & (orders_df['Order ID'] == order_id))]

    # Save all files
    orders_df.to_excel(ORDER_FILE, index=False)  # Save orders back to the Excel file
    inventory_df.to_excel(INVENTORY_FILE, index=False)  # Save updated inventory
    order_logs_df.to_excel(INVENTORY_LOGS_FILE, index=False)  # Save order logs

    return redirect(url_for('admin_orders'))

@app.route('/update_order_status', methods=['POST'])
def update_order_status():
    data = request.get_json()
    order_id = data.get('order_id')
    new_status = data.get('status')

    if not order_id or not new_status:
        return jsonify(success=False, error="Missing data"), 400

    df = load_orders()
    updated = False

    if 'Order ID' in df.columns and 'Status' in df.columns:
        idx = df[df['Order ID'] == order_id].index
        if not idx.empty:
            df.loc[idx, 'Status'] = new_status
            updated = True
            df.to_excel(ORDER_FILE, index=False)

    if updated:
        return jsonify(success=True)
    else:
        return jsonify(success=False, error="Order ID not found"), 404

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username and password:
            user = users.get(username)
            if user and user['password'] == password:
                session['username'] = username
                session['is_admin'] = user['admin']
                flash('Login successful!', 'success')
                return redirect(url_for('home'))
            else:
                flash('Invalid username or password!', 'danger')
        else:
            flash('Please enter both username and password!', 'warning')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out!', 'info')
    return redirect(url_for('login'))

if __name__ == '__main__':
    app.run(debug=True)
