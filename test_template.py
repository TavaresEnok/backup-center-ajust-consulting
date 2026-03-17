from app import create_flask_app

app = create_flask_app()

with app.test_request_context('/tenant/test/dashboard'):
    from flask import render_template
    try:
        result = render_template(
            'tenant/dashboard.html',
            tenant=None,
            tenant_slug='test',
            stats={'total_devices': 0, 'online_devices': 0, 'offline_devices': 0, 'storage_used': '0MB'},
            recent_backups=[],
            chart_data='{}'
        )
        print("SUCCESS! Template rendered.")
        print(result[:500])
    except Exception as e:
        import traceback
        print("ERROR!")
        traceback.print_exc()
