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
    lead_id = fields.Many2one('crm.lead',string='Originating Lead/Opportunity',help="The marketing or sales lead where this business originated.")
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
        ('cancelled', 'Cancelled'),
        ('invoice_paid', 'Invoice Paid')
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
        """ GATES: Validates Signed Quotation & Ingest Fields. 
                  Warns for Customer Debt & Duplicate Open Files. """
        self.ensure_one()

        # 1. HARD GATE: Ensure a Sale Order/Quotation relation is picked
        if not self.sale_order_id:
            raise UserError(_("Validation Error: No Quotation is linked to this Repair Order. Please link a quotation before workshop intake."))
        
        # 2. HARD GATE: Ensure that Sale Order is SIGNED / APPROVED (State must be 'sale' or 'done')
        if self.sale_order_id.state not in ['sale', 'done']:
            raise UserError(_("Workflow Block: The linked quotation (%s) is not approved. It must be confirmed/signed before you can receive the vehicle.") % self.sale_order_id.name)

        # 3. HARD GATE: Asset Ingest Fields Check
        if not self.vehicle_license_plate or self.vehicle_mileage <= 0:
            raise UserError(_("Data Missing: Please record a valid license plate and current mileage before checking the vehicle into the shop floor."))
        
        # 4. WARNING ONLY: Parallel Duplicate Work Order Scan (Non-blocking)
        duplicate_runs = self.env['automotive.order'].search_count([
            ('vehicle_id', '=', self.vehicle_id.id),
            ('state', 'in', ['received', 'progress', 'ready']),
            ('id', '!=', self.id)
        ])
        if duplicate_runs > 0:
            self.message_post(body=Markup(
                "<span style='color: #ee9b00;'>⚠️ <b>Intake Warning:</b> This vehicle asset is currently active in another running workshop file. Processing intake anyway.</span>"
            ))

        # 5. WARNING ONLY: Customer Outstanding Accounting Balance Scan (Non-blocking)
        unpaid_invoices_count = self.env['account.move'].search_count([
            ('partner_id', '=', self.partner_id.id),
            ('payment_state', 'not in', ['paid', 'in_payment']),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted')
        ])
        if unpaid_invoices_count > 0:
            self.message_post(body=Markup(
                "<span style='color: #d90429;'>⚠️ <b>Financial Warning:</b> This customer has outstanding, unpaid invoices on file! Processing intake anyway.</span>"
            ))

        # SUCCESS: Update sequence to 'Vehicle Received'
        self.message_post(body=Markup("<span style='color:green;'>⚙️ <b>Intake Passed:</b> Vehicle successfully received. Upstream quotation approval verified.</span>"))
        self.write({'state': 'received'})

    def action_start_repair(self):
        """ Advances to Work In Progress. Enforces technician assignment. """
        self.ensure_one()
        if not self.assigned_technician_id:
            raise UserError(_("Operational Gate: Please assign a Technician before marking this file as Work In Progress (WIP)."))
        
        self.write({'state': 'progress'})
        self.message_post(body="🔧 <b>Workshop Notice:</b> Repair activities have actively commenced on the shop floor.")

    def action_make_ready(self):
        """ Advances to Ready for Pickup once technician finishes wrenching. """
        self.ensure_one()
        self.write({'state': 'ready'})
        self.message_post(body="✅ <b>Workshop Notice:</b> Work completed. Vehicle is ready for customer collection.")

    def action_deliver_vehicle(self):
        """ Advances to Delivered upon handover. """
        self.ensure_one()
        self.write({'state': 'delivered'})
        self.message_post(body="🚗 <b>Hand-off Complete:</b> Vehicle keys safely handed over and delivered to customer.")

    def action_cancel(self):
        """ Allows cancellation from early stages """
        self.ensure_one()
        if self.state in ['progress', 'ready', 'delivered']:
            raise UserError(_("Security Block: Cannot cancel a repair order that has already been executed or delivered."))
        self.write({'state': 'cancelled'})

    # -------------------------------------------------------------------------
    # ODOO OVRRIDES (Sequential Tracking)
    # -------------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        """ Automatically attaches custom sequence tracking numbers upon creation """
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('automotive.order') or _('New')
        return super(AutomotiveOrder, self).create(vals_list)  
    
    def action_check_invoice_payment(self):
        """ Can be called via an automated action or a button to check payment status """
        self.ensure_one()
        
        if self.sale_order_id and self.sale_order_id.invoice_ids:
        # Check if all invoices linked to the sale order are fully paid
          invoices = self.sale_order_id.invoice_ids.filtered(lambda r: r.move_type == 'out_invoice' and r.state == 'posted')
        if invoices and all(inv.payment_state in ['paid', 'in_payment'] for inv in invoices):
            self.write({'state': 'invoice_paid'})
            self.message_post(body="💰 <b>Payment Confirmed:</b> Linked invoice has been fully paid. Workshop file closed successfully.")
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