from odoo import api, fields, models
from odoo.exceptions import ValidationError


class VolumeDiscountTier(models.Model):
    """Tramo de la escala de bonificación por volumen mensual.

    Sin cliente: tramo de la escala general de la empresa.
    Con cliente: tramo de la escala particular de ese cliente, que reemplaza
    completa a la general (no se mezclan tramos de las dos).
    """

    _name = "yaguven.volume.discount.tier"
    _description = "Tramo de bonificación por volumen"
    _order = "company_id, partner_id, amount_from"

    company_id = fields.Many2one(
        "res.company", string="Empresa", required=True, index=True,
        default=lambda self: self.env.company,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Cliente", index=True, ondelete="cascade",
        domain="[('is_company', '=', True)]",
        help="Vacío: tramo de la escala general de la empresa.",
    )
    currency_id = fields.Many2one(related="company_id.currency_id")
    amount_from = fields.Monetary(
        string="Compra mensual desde", required=True,
        help="Monto facturado en el mes calendario a partir del cual rige el porcentaje.",
    )
    percent = fields.Float(string="Bonificación (%)", required=True, digits=(5, 2))

    _tier_unique = models.UniqueIndex(
        "(company_id, COALESCE(partner_id, 0), amount_from)",
        "Ya existe un tramo con ese monto en la misma escala.",
    )

    @api.constrains("amount_from", "percent")
    def _check_values(self):
        for tier in self:
            if tier.amount_from <= 0:
                raise ValidationError(self.env._("El monto del tramo tiene que ser mayor a cero."))
            if not 0 < tier.percent < 100:
                raise ValidationError(self.env._("El porcentaje tiene que estar entre 0 y 100."))

    @api.model
    def _get_scale(self, company, partner):
        """Escala vigente para (empresa, cliente): la del cliente si tiene, si no la general."""
        partner = partner.commercial_partner_id
        domain = [("company_id", "=", company.id)]
        tiers = self.search(domain + [("partner_id", "=", partner.id)])
        return tiers or self.search(domain + [("partner_id", "=", False)])

    @api.model
    def _percent_for(self, scale, amount):
        """Porcentaje del tramo más alto alcanzado por `amount` (0 si no llega a ninguno)."""
        reached = scale.filtered(lambda t: amount >= t.amount_from)
        return max(reached.mapped("percent"), default=0.0)
