from app import app

client = app.test_client()

dashboard = client.get('/dashboard')
print('dashboard_status', dashboard.status_code)
print('dashboard_has_hero', 'Welcome back' in dashboard.get_data(as_text=True))

admin = client.get('/admin')
print('admin_status', admin.status_code)
print('admin_has_hero', 'Operations overview' in admin.get_data(as_text=True))
