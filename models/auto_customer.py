from odoo import models, fields, api, _
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
