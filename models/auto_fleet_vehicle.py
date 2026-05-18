from odoo import models, fields, api, _
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
