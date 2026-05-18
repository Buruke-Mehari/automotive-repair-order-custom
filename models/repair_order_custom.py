# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html_escape
from markupsafe import Markup

class AutomotiveOrder(models.Model):
    _name = 'automotive.order'
    _description = 'Automotive Repair Order'
    _inherit = ['mail.thread', 'mail.activity.mixin']

    # =========================================================================
    # STANDARD & RELATIONAL FIELDS
    # =========================================================================
    name = fields.Char(string='Reference', required=True, copy=False, readonly=True, default=lambda self: _('New'))
    company_id = fields.Many2one('res.company', string='Company', required=True, default=lambda self: self.env.company)
    partner_id = fields.Many2one('res.partner', string='Customer', required=True, tracking=True)
    sale_order_id = fields.Many2one('sale.order', string='Sale Order/Quotation', tracking=True)
    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle', required=True, tracking=True)
    
    create_uid = fields.Many2one('res.users', string='Created By', readonly=True)
    create_date = fields.Datetime(string='Creation Date', readonly=True)
    
    vehicle_license_plate = fields.Char(string='License Plate', required=True, tracking=True)
    vehicle_mileage = fields.Integer(string='Mileage', tracking=True)
    date_in = fields.Datetime(string='Date/Time In', default=fields.Datetime.now, required=True, tracking=True)
    date_delivered = fields.Datetime(string='Final Handoff Time', readonly=True, tracking=True)
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

    # Signatures
    customer_signature = fields.Binary(string="Customer Signature", copy=False)
    customer_signed_by = fields.Char(string="Customer Sign", copy=False, tracking=True)
    customer_signed_on = fields.Datetime(string="Customer Signed Date", copy=False, tracking=True)

    tech_signature = fields.Binary(string="Technician Signature", copy=False)
    tech_signed_by = fields.Char(string="Technician Sign", copy=False, tracking=True)
    tech_signed_on = fields.Datetime(string="Tech Signed Date", copy=False, tracking=True)


    # =========================================================================
    # HARD SEQUENCING VALIDATIONS & CONSTRAINTS
    # =========================================================================
    @api.constrains('state', 'sale_order_id')
    def _check_workflow_gates(self):
        """Enforces systemic validation controls between steps"""
        # Load user-controlled administrative params globally if needed
        enforce_quote = self.env['ir.config_parameter'].sudo().get_param('automotive.enforce_quote_before_ro', default=True)

        for record in self:
            # Gate 1: Check-in without quotation context
            if enforce_quote and record.state == 'received' and not record.sale_order_id:
                raise ValidationError(_("Workflow Block: You cannot advance an Automotive Order to 'Received' without a valid, linked Quotation / Sale Order."))

            if record.state == 'received' and record.sale_order_id and record.sale_order_id.state not in ['sale', 'done']:
                raise ValidationError(_("Workflow Block: The associated Sale Order/Quotation must be fully confirmed or signed before matching vehicle reception."))

            # Gate 2: Block invoicing operations on a cancelled order trail
            if record.state == 'cancelled' and record.sale_order_id and record.sale_order_id.invoice_status == 'to invoice':
                raise ValidationError(_("Security Intercept: Creating invoices or tracking lines against a cancelled repair run is fundamentally blocked."))

    # =========================================================================
    # AUTOMATION WORKFLOW ACTIONS & STATUS UPDATES
    # =========================================================================
    def action_receive_vehicle(self):
     self.ensure_one()

    # 1. Hard block for missing critical info (Keep this blocking)
     if not self.vehicle_license_plate or self.vehicle_mileage <= 0:
        raise UserError(_("Data Missing: Please record a valid license plate and current mileage."))
    
    # 2. Duplicate Check - WARNING ONLY (Logs to Chatter, does NOT block)
        duplicate_runs = self.env['automotive.order'].search_count([
            ('vehicle_id', '=', self.vehicle_id.id),
            ('state', 'in', ['received', 'progress', 'ready']),
            ('id', '!=', self.id)
            ])
        if duplicate_runs > 0:
         self.message_post(body=Markup(
            "<span style='color: #ee9b00;'>⚠️ <b>Intake Warning:</b> This vehicle is already active in another job sheet. Proceeding anyway.</span>"
        ))

    # 3. Unpaid Balance Check - WARNING ONLY (Logs to Chatter, does NOT block)
        unpaid_invoices_count = self.env['account.move'].search_count([
            ('partner_id', '=', self.partner_id.id),
            ('payment_state', 'not in', ['paid', 'in_payment']),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted')
            ])
        if unpaid_invoices_count > 0:
         self.message_post(body=Markup(
            "<span style='color: #d90429;'>⚠️ <b>Financial Warning:</b> Customer has outstanding unpaid invoices! Proceeding with intake anyway.</span>"
        ))

    # 4. Move forward with state change seamlessly
        self.write({'state': 'received'})
              
    def action_start_repair(self):
        if not self.user_id:
            raise UserError(_("Operation Blocked: An assigned technician is required to start work in progress operations."))
        self.write({'state': 'progress'})

    def action_set_ready(self):
        if not self.tech_signature:
            raise UserError(_("QA Failure: Technician must sign off work execution parameters before marking the asset as Ready."))
        self.write({'state': 'ready'})

    def action_deliver(self):
        if self.state != 'ready':
            raise UserError(_("Workflow Exception: Direct transition to Delivered from a state other than 'Ready' is not allowed."))
        
        # Capture precise handoff tracking metrics automatically
        self.write({
            'state': 'delivered',
            'date_delivered': fields.Datetime.now()
        })
        self.message_post(body=_("🔑 Vehicle physically delivered to client. Delivery record timestamp logged successfully."))

    def action_cancel(self):
        self.write({'state': 'cancelled'})   

    # =========================================================================
    # AUTOMATED INVOICE SCRIPT GENERATOR
    # =========================================================================
    def action_create_final_invoice(self):
        """Generates customer draft invoice using standard confirmed Sales order data lines"""
        self.ensure_one()
        if not self.sale_order_id:
            raise UserError(_("Traceback Error: No Source sale context found to build transactional revenue lines against."))
        
        if self.state not in ['ready', 'delivered']:
            raise UserError(_("Billing Exception: Final Invoice runs are locked until workshop procedures hit 'Ready' or 'Delivered'."))

        # Leverage standard system functions to preserve down payments or specific parameters
        invoice = self.sale_order_id._create_invoices(final=True)
        
        # Push confirmation log down to Chatter timeline
        self.message_post(body=Markup(_("🧾 Draft Invoice <b>%s</b> generated automatically via linked sales items configuration.") % invoice.name))
        
        return {
            'name': _('Customer Invoice'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': invoice.id,
            'target': 'current',
        }

    # =========================================================================
    # SIGNATURE AUTO-UPDATES
    # =========================================================================
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
                self.tech_signed_by = self.env.user.name

    # Keep original mail composer method intact below...
    def action_send_repair_order_email(self):
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
            'target': 'new',
            'context': ctx,
        }