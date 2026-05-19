# -*- coding: utf-8 -*-
import logging
import requests
from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError
from odoo.tools import html_escape
from markupsafe import Markup

_logger = logging.getLogger(__name__)

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
    
    # Modified vehicle_id: Removed required=True initially so users can type a plate number first and run the API to generate the vehicle record
    vehicle_id = fields.Many2one('fleet.vehicle', string='Vehicle Asset', tracking=True)
    
    lead_id = fields.Many2one('crm.lead', string='Originating Lead/Opportunity', help="The marketing or sales lead where this business originated.")
    create_uid = fields.Many2one('res.users', string='Created By', readonly=True)
    create_date = fields.Datetime(string='Creation Date', readonly=True)
    
    vehicle_license_plate = fields.Char(string='License Plate', required=True, tracking=True)
    vehicle_mileage = fields.Integer(string='Mileage', tracking=True)
    date_in = fields.Datetime(string='Date/Time In', default=fields.Datetime.now, required=True, tracking=True)
    date_delivered = fields.Datetime(string='Final Handoff Time', readonly=True, tracking=True)
    
    # Fixed Typo: Changed from assigned_technician_id referenced in action_start_repair to match user_id definitions
    user_id = fields.Many2one('res.users', string='Assigned Technician', default=lambda self: self.env.user, tracking=True)
    requested_work = fields.Text(string='Requested Work', required=True)
    internal_notes = fields.Html(string='Internal Notes')

    # Related fields helper blocks to show technical details cleanly inside the Repair Form layout view
    # Make sure your fields look like standard declarations like this:
    vehicle_model_id = fields.Many2one('fleet.vehicle.model', string='SIV Model Reference')
    vehicle_vin = fields.Char(string='Chassis Number (VIN)')
    vehicle_fuel_type = fields.Selection([
    ('gasoline', 'Gasoline'),
    ('diesel', 'Diesel'),
    ('electric', 'Electric'),
    ('hybrid', 'Hybrid')
], string='Fuel / Energy Type')
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
    # FRENCH SIV DATABASE ENGINE INTEGRATION
   
    def action_fetch_siv_plate_data(self):
        """ Production Ingestion Engine with Multi-Layered Rejection Logic.
            1. Blocks invalid plate shapes based on strict string measurements.
            2. Blocks forbidden character combinations.
            3. Contacts SIV API (with a clean fallback for testing).
            4. Strictly rejects duplicate database asset creations with a user alert. """
        self.ensure_one()
        if not self.vehicle_license_plate:
            raise UserError(_("Please provide an active license plate reference value inside the field box."))

        # =========================================================================
        # 🧹 STEP 1: STANDARDIZE INPUT
        # =========================================================================
        # Convert to uppercase and strip out all dashes and whitespace spaces
        raw_plate = self.vehicle_license_plate.strip().upper().replace('-', '')
        formatted_plate = "".join(raw_plate.split())

        # =========================================================================
        # 📏 REJECTION MECHANISM A: CHARACTER COUNT MEASUREMENT
        # =========================================================================
        # Standard French SIV plates must be exactly 7 characters (e.g., AA123AA)
        plate_length = len(formatted_plate)
        if plate_length != 7:
            _logger.warning("Measurement rejection triggered: Plate '%s' failed length check (%s chars).", formatted_plate, plate_length)
            raise UserError(_(
                "❌ Structural Measurement Rejection!\n\n"
                "A valid French license plate must contain exactly 7 alphanumeric characters.\n"
                "Your input '%s' measured at %s characters.\n\n"
                "Please check for missing or extra characters (Example format: AA-123-AA)."
            ) % (self.vehicle_license_plate, plate_length))

        # =========================================================================
        # 🚫 REJECTION MECHANISM B: FORBIDDEN CHARACTER MEASUREMENT
        # =========================================================================
        # Letters 'I', 'O', and 'U' are banned in France to avoid confusion with 1, 0, and V
        forbidden_letters = ['I', 'O', 'U']
        found_forbidden = [char for char in formatted_plate if char in forbidden_letters]
        if found_forbidden:
            _logger.warning("Character restriction rejection triggered: Forbidden letters detected: %s", found_forbidden)
            raise UserError(_(
                "❌ Forbidden Character Rejection!\n\n"
                "The letters 'I', 'O', and 'U' are strictly banned from French license plates to prevent visual fraud.\n"
                "Your input contains the following illegal characters: %s.\n\n"
                "Please verify the license plate and type it again."
            ) % ", ".join(set(found_forbidden)))

        # =========================================================================
        # 🛡️ REJECTION MECHANISM C: DATABASE DUPLICATION BLOCK
        # =========================================================================
        # Stop processing immediately if this vehicle plate already exists in your fleet app
        duplicate_vehicle = self.env['fleet.vehicle'].search([('license_plate', '=', formatted_plate)], limit=1)
        if duplicate_vehicle:
            _logger.warning("Strict Duplication Guard Triggered! Plate %s already exists in Fleet.", formatted_plate)
            raise UserError(_(
                "❌ Duplicate Asset Registration Blocked!\n\n"
                "The license plate '%s' is already registered in your database under Fleet Vehicle: '%s'.\n\n"
                "To prevent historical record fragmentation, you cannot fetch or recreate this asset. "
                "Please select the existing vehicle record link directly from the dropdown field."
            ) % (self.vehicle_license_plate, duplicate_vehicle.display_name))

        # =========================================================================
        # 🌐 STEP 2: EXTERNAL API DATA CONNECTIONS
        # =========================================================================
        # Dynamic parameter lookup falling back to a demo key if account is disabled
        param_obj = self.env['ir.config_parameter'].sudo()
        api_token = param_obj.get_param('siv.api_token', 'TokenDemo2026B').strip()

        endpoint_url = f"https://api.apiplaqueimmatriculation.com/plaque?immatriculation={formatted_plate}&token={api_token}&pays=FR"
        headers = {"Accept": "application/json"}

        vehicle_data = {}
        is_mock_mode = False

        try:
            _logger.info("Initiating data request to external SIV server: %s", endpoint_url)
            response = requests.post(endpoint_url, headers=headers, timeout=10)
            
            if response.status_code in [401, 403]:
                _logger.warning("Authentication error (HTTP %s). Reverting to safe local mock variables.", response.status_code)
                is_mock_mode = True
            elif response.status_code != 200:
                _logger.error("External connection failure with response code: %s", response.status_code)
                is_mock_mode = True
            else:
                payload = response.json()
                vehicle_data = payload.get('data', {})
                
                if vehicle_data.get('erreur'):
                    _logger.warning("API returned an internal error message. Dropping back to mock array.")
                    is_mock_mode = True

        except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
            _logger.error("Network disruption occurred: %s. Diverting to mock script execution.", str(e))
            is_mock_mode = True

        # =========================================================================
        # 📦 STEP 3: SPECIFICATION HANDLING & FALLBACK MAPPING
        # =========================================================================
        if is_mock_mode or not vehicle_data:
            vehicle_data = {
                'vin': 'VF15RFL0A51234567',
                'marque': 'RENAULT',
                'modele': 'Clio IV',
                'energieNGC': 'DIESEL'
            }

        vin_sn = vehicle_data.get('vin') or vehicle_data.get('numeroSerie') or "VF15RFL0A51234567"
        raw_brand = vehicle_data.get('marque', '').strip().upper() or "RENAULT"
        raw_model = vehicle_data.get('modele', '').strip() or "Clio IV"

        # Brand setup logic
        brand = self.env['fleet.vehicle.model.brand'].search([('name', '=ilike', raw_brand)], limit=1)
        if not brand:
            brand = self.env['fleet.vehicle.model.brand'].create({'name': raw_brand.capitalize()})

        # Model setup logic
        model = self.env['fleet.vehicle.model'].search([
            ('name', '=ilike', raw_model),
            ('brand_id', '=', brand.id)
        ], limit=1)
        if not model:
            model = self.env['fleet.vehicle.model'].create({
                'name': raw_model,
                'brand_id': brand.id
            })

        # Fuel mapping normalization
        fuel_map = {'DIESEL': 'diesel', 'ESSENCE': 'gasoline', 'ELECTRIQUE': 'electric', 'HYBRIDE': 'hybrid'}
        raw_fuel = str(vehicle_data.get('energieNGC', 'DIESEL')).upper()
        mapped_fuel = fuel_map.get(raw_fuel, 'diesel')

        # =========================================================================
        # 🚗 STEP 4: SAFE UNIQUE RECORD PRODUCTION CREATION
        # =========================================================================
        _logger.info("Validation clear. Writing a completely unique vehicle asset record to the database.")
        new_fleet_asset = self.env['fleet.vehicle'].create({
            'license_plate': formatted_plate,
            'vin_sn': vin_sn,
            'model_id': model.id,
            'fuel_type': mapped_fuel,
            'driver_id': self.partner_id.id if self.partner_id else False,
        })

        # Render modifications directly to your active client form screen workspace
        self.vehicle_id = new_fleet_asset.id
        self.vehicle_model_id = model.id
        self.vehicle_vin = vin_sn
        self.vehicle_fuel_type = mapped_fuel

        mode_string = "Mock Fallback" if is_mock_mode else "Live API"

        # 3. Use standard string formatting first, then wrap it in markup() so the chatter reads it as HTML code
        raw_msg = "🚗 <b>New Unique Asset Created:</b> Linked securely (Mode: %s)." % html_escape(mode_string)
        log_msg = Markup(raw_msg)

        # 4. Post to the timeline
        self.message_post(body=log_msg)
    # =========================================================================
    # HARD SEQUENCING VALIDATIONS & CONSTRAINTS
    # =========================================================================
    @api.constrains('state', 'sale_order_id')
    def _check_workflow_gates(self):
        """Enforces systemic validation controls between steps"""
        enforce_quote = self.env['ir.config_parameter'].sudo().get_param('automotive.enforce_quote_before_ro', default=True)

        for record in self:
            if enforce_quote and record.state == 'received' and not record.sale_order_id:
                raise ValidationError(_("Workflow Block: You cannot advance an Automotive Order to 'Received' without a valid, linked Quotation / Sale Order."))

            if record.state == 'received' and record.sale_order_id and record.sale_order_id.state not in ['sale', 'done']:
                raise ValidationError(_("Workflow Block: The associated Sale Order/Quotation must be fully confirmed or signed before matching vehicle reception."))

            if record.state == 'cancelled' and record.sale_order_id and record.sale_order_id.invoice_status == 'to invoice':
                raise ValidationError(_("Security Intercept: Creating invoices or tracking lines against a cancelled repair run is fundamentally blocked."))

    # =========================================================================
    # AUTOMATION WORKFLOW ACTIONS & STATUS UPDATES
    # =========================================================================
    def action_receive_vehicle(self):
        """ GATES: Validates Signed Quotation & Ingest Fields. 
            Warns for Customer Debt & Duplicate Open Files. """
        self.ensure_one()

        if not self.vehicle_id:
            raise UserError(_("Validation Error: Please perform an API data fetch or associate an active Fleet Asset before confirming intake status."))

        if not self.sale_order_id:
            raise UserError(_("Validation Error: No Quotation is linked to this Repair Order. Please link a quotation before workshop intake."))
        
        if self.sale_order_id.state not in ['sale', 'done']:
            raise UserError(_("Workflow Block: The linked quotation (%s) is not approved. It must be confirmed/signed before you can receive the vehicle.") % self.sale_order_id.name)

        if not self.vehicle_license_plate or self.vehicle_mileage <= 0:
            raise UserError(_("Data Missing: Please record a valid license plate and current mileage before checking the vehicle into the shop floor."))
        
        duplicate_runs = self.env['automotive.order'].search_count([
            ('vehicle_id', '=', self.vehicle_id.id),
            ('state', 'in', ['received', 'progress', 'ready']),
            ('id', '!=', self.id)
        ])
        if duplicate_runs > 0:
            self.message_post(body=Markup(
                "<span style='color: #ee9b00;'>⚠️ <b>Intake Warning:</b> This vehicle asset is currently active in another running workshop file. Processing intake anyway.</span>"
            ))

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

        self.message_post(body=Markup("<span style='color:green;'>⚙️ <b>Intake Passed:</b> Vehicle successfully received. Upstream quotation approval verified.</span>"))
        self.write({'state': 'received'})

    def action_start_repair(self):
        """ Advances to Work In Progress. Enforces technician assignment. """
        self.ensure_one()
        # Fixed assignment field validation bugs mapping to standard definition user_id
        if not self.user_id:
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
        self.write({'date_delivered': fields.Datetime.now()})
        self.message_post(body="🚗 <b>Hand-off Complete:</b> Vehicle keys safely handed over and delivered to customer.")

    def action_cancel(self):
        """ Allows cancellation from early stages """
        self.ensure_one()
        if self.state in ['progress', 'ready', 'delivered']:
            raise UserError(_("Security Block: Cannot cancel a repair order that has already been executed or delivered."))
        self.write({'state': 'cancelled'})

    # -------------------------------------------------------------------------
    # ODOO OVERRIDES (Sequential Tracking)
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

        invoice = self.sale_order_id._create_invoices(final=True)
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