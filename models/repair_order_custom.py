from odoo import models, fields, api, _
from odoo.exceptions import UserError
from odoo.tools import html_escape
from markupsafe import Markup 


class AutomotiveOrder(models.Model):
    _name = 'automotive.order'
    _description = 'Automotive Repair Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # =========================================================================
    # STANDARD FIELDS (With Native Tracking)
    # =========================================================================
    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    
    sale_order_id = fields.Many2one('sale.order', string='Sale Order', tracking=True)
  
    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle', required=True, tracking=True)
    
    # Track Creator/Date automatically in database and initial log card
    create_uid = fields.Many2one('res.users', string='Created By', readonly=True)
    create_date = fields.Datetime(string='Creation Date', readonly=True)
    
    vehicle_license_plate = fields.Char(string='License Plate', required=True, tracking=True)
    vehicle_mileage = fields.Integer(string='Mileage', tracking=True)
    date_in = fields.Datetime(string='Date/Time In', default=fields.Datetime.now, required=True, tracking=True)
    user_id = fields.Many2one('res.users', string='Assigned Technician', default=lambda self: self.env.user, tracking=True)
    requested_work = fields.Text(string='Requested Work', required=True)
    internal_notes = fields.Html(string='Internal Notes')

    # Status Tracking
    state = fields.Selection([
        ('draft', 'Draft'),
        ('received', 'Vehicle Received'),
        ('progress', 'In Progress'),
        ('ready', 'Ready'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft', tracking=True, required=True)

    
    
    customer_signature = fields.Binary(string="Customer Signature", copy=False) # No tracking here to prevent crashes
    customer_signed_by = fields.Char(string="Customer Sign", copy=False, tracking=True)
    customer_signed_on = fields.Datetime(string="Customer Signed Date", copy=False, tracking=True)

    tech_signature = fields.Binary(string="Technician Signature", copy=False) # No tracking here to prevent crashes
    tech_signed_by = fields.Char(string="Technician Sign", copy=False, tracking=True)
    tech_signed_on = fields.Datetime(string="Tech Signed Date", copy=False, tracking=True)

  
    @api.onchange('customer_signature')
    def _onchange_customer_signature(self):
        if self.customer_signature and not self.customer_signed_on:
            self.customer_signed_on = fields.Datetime.now()

    @api.onchange('tech_signature')
    def _onchange_tech_signature(self):
        if self.tech_signature:
            if not self.tech_signed_on:
                self.tech_signed_on = fields.Datetime.now()
            if not self.tech_signed_by:
                self.tech_signed_by = "Signed"


    def write(self, vals):
        return super(AutomotiveOrder, self).write(vals)

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

   # --- EMAIL COMPOSER METHOD ---
    def action_send_repair_order_email(self):
        """ Opens standard Odoo email composition wizard with pre-filled template and PDF """
        self.ensure_one()
        
        template = self.env.ref('repair_order_custom.email_template_automotive_order', raise_if_not_found=False)
        
        if not template:
            raise UserError(_("The email template for Automotive Repair Orders could not be found."))

        ctx = {
            'default_model': 'automotive.order',
            'default_res_ids': [self.id], 
            'default_use_template': bool(template.id),
            'default_template_id': template.id,
            'default_composition_mode': 'comment',
            'mark_so_as_sent': True, 
            'force_email': True,
        }

        return {
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_model': 'mail.compose.message',
            'views': [(False, 'form')],
            'view_id': False,
            'target': 'new',
            'context': ctx,
        }

# =============================================================================
# PARTNER INHERITANCE (Customer Smart Button Engine)
# =============================================================================
class ResPartner(models.Model):
    _inherit = 'res.partner'

    repair_order_count = fields.Integer(compute='_compute_repair_order_count', string='Repair Orders Count')

    def _compute_repair_order_count(self):
        for partner in self:
            partner.repair_order_count = self.env['automotive.order'].search_count([
                ('partner_id', 'child_of', partner.ids)
            ])

    def action_view_repair_orders(self):
        self.ensure_one()
        return {
            'name': _('Automotive Repair Orders'),
            'type': 'ir.actions.act_window',
            'res_model': 'automotive.order',
            'view_mode': 'tree,form',
            'domain': [('partner_id', 'child_of', self.ids)],
            'context': {'default_partner_id': self.id},
        }


# =============================================================================
# FLEET VEHICLE INHERITANCE (Vehicle Smart Button Engine)
# =============================================================================
class FleetVehicle(models.Model):
    _inherit = 'fleet.vehicle'

    repair_order_count = fields.Integer(compute='_compute_repair_order_count', string='Repair Orders Count')

    def _compute_repair_order_count(self):
        for vehicle in self:
            vehicle.repair_order_count = self.env['automotive.order'].search_count([
                ('vehicle_id', '=', vehicle.id)
            ])

    def action_view_repair_orders(self):
        self.ensure_one()
        return {
            'name': _('Repair History'),
            'type': 'ir.actions.act_window',
            'res_model': 'automotive.order',
            'view_mode': 'tree,form',
            'domain': [('vehicle_id', '=', self.id)],
            'context': {'default_vehicle_id': self.id, 'default_partner_id': self.driver_id.id or False},
        }

  

 
   