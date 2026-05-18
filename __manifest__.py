# -*- coding: utf-8 -*-
{
    'name': 'Automotive',
    'version': '17.0.1.0.0',
    'summary': 'Lightweight and clean vehicle repair order workflow',
    'category': 'Services',
    'depends': ['base', 'mail', 'sale','fleet'],
    'data': [
        'security/ir.model.access.csv',
        'security/security_group.xml',
        'views/repair_order_custom_view.xml',
        'data/ir_sequence_data.xml',
        'data/mail_template_data.xml',
        'report/repair_order_report.xml',
        'views/auto_customer_view.xml',
        'views/auto_fleet_view.xml'
       
    ],
    'installable': True,
    'application': False,
    'license': 'LGPL-3',
}