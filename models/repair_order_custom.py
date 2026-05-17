from odoo import models, fields, api, _

class AutomotiveOrder(models.Model):
    _name = 'automotive.order'
    _description = 'Automotive Repair Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'id desc'

    # FIXED: Added the missing name field for sequences
    name = fields.Char(
        string='Reference', 
        required=True, 
        copy=False, 
        readonly=True, 
        default=lambda self: _('New')
    )
    
    company_id = fields.Many2one(
        'res.company', 
        string='Company', 
        required=True, 
        default=lambda self: self.env.company
    )
    
    partner_id = fields.Many2one(
        'res.partner', 
        string='Customer', 
        required=True, 
        tracking=True
    )
    
    # Vehicle fields
    vehicle_license_plate = fields.Char(string='License Plate', required=True, tracking=True)
    vehicle_mileage = fields.Integer(string='Mileage', tracking=True)
    
    # Dates & Assignments
    date_in = fields.Datetime(
        string='Date/Time In', 
        default=fields.Datetime.now, 
        required=True, 
        tracking=True
    )
    user_id = fields.Many2one(
        'res.users', 
        string='Assigned Technician', 
        default=lambda self: self.env.user, 
        tracking=True
    )
    sale_order_id = fields.Many2one(
        'sale.order', 
        string='Related Quotation', 
        tracking=True
    )
    requested_work = fields.Text(string='Requested Work', required=True)
    internal_notes = fields.Html(string='Internal Notes')
    
    state = fields.Selection([
        ('draft', 'Draft'),
        ('received', 'Vehicle Received'),
        ('progress', 'In Progress'),
        ('ready', 'Ready'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft', tracking=True, required=True)

    # --- SIGNATURE SYSTEM FIELDS ---
    
    # 1. Customer Drop-off Signature
    customer_signature = fields.Binary(string="Customer Signature", copy=False)
    customer_signed_by = fields.Char(string="Signed By (Name)", copy=False)
    customer_signed_on = fields.Datetime(string="Signed On", copy=False)

    # 2. Technician Completion Signature
    tech_signature = fields.Binary(string="Technician Signature", copy=False)
    tech_signed_on = fields.Datetime(string="Tech Signed On", copy=False)
    
    @api.onchange('customer_signature')
    def _onchange_customer_signature(self):
        if self.customer_signature and not self.customer_signed_on:
            self.customer_signed_on = fields.Datetime.now()

    @api.onchange('tech_signature')
    def _onchange_tech_signature(self):
        if self.tech_signature and not self.tech_signed_on:
            self.tech_signed_on = fields.Datetime.now()
    
    
    
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                # 1. Look for the company set on the record, fallback to current active company
                company_id = vals.get('company_id') or self.env.company.id
                
                # 2. Tell the sequence engine to execute within that company's rule scope
                vals['name'] = self.env['ir.sequence'].with_context(
                    with_company=company_id
                ).next_by_code('automotive.order.seq') or _('New')
                
        return super(AutomotiveOrder, self).create(vals_list)
    # Simple workflow stage transitions
    def action_receive_vehicle(self):
        self.write({'state': 'received'})

    def action_start_repair(self):
        self.write({'state': 'progress'})

    def action_set_ready(self):
        self.write({'state': 'ready'})

    def action_deliver(self):
        self.write({'state': 'delivered'})

    def action_cancel(self):
        self.write({'state': 'cancelled'})