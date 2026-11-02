from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestVolumeDiscount(AccountTestInvoicingCommon):
    """Ejemplo de referencia: 4% desde 5.000.000 y 5% desde 10.000.000 en el mes.

    Facturas del 5, 15 y 27 por 1, 2 y 3 millones: la del 27 cruza el primer tramo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.bonus_product = cls.env["product.product"].create({
            "name": "Bonificación por volumen", "type": "service", "taxes_id": [fields.Command.clear()],
        })
        cls.product_a.taxes_id = [fields.Command.clear()]
        cls.company.write({
            "vd_product_id": cls.bonus_product.id,
            "vd_base": "untaxed",
            "vd_only_paid": False,
            "vd_tier_ids": [
                fields.Command.create({"amount_from": 5_000_000, "percent": 4}),
                fields.Command.create({"amount_from": 10_000_000, "percent": 5}),
            ],
        })

    # ----------------------------------------------------------------- helpers

    def _invoice(self, day, amount, partner=None, post=True):
        move = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": (partner or self.partner_a).id,
            "invoice_date": f"2026-11-{day:02d}",
            "invoice_line_ids": [fields.Command.create({
                "product_id": self.product_a.id, "quantity": 1, "price_unit": amount,
                "tax_ids": [fields.Command.clear()],
            })],
        })
        if post:
            move.with_context(vd_checked=True).action_post()
        return move

    def _wizard(self, move):
        action = move.action_post()
        self.assertEqual(action.get("res_model"), "yaguven.volume.discount.wizard",
                         "Tiene que abrir el asistente en vez de confirmar")
        self.assertEqual(move.state, "draft")
        # Igual que el cliente web: los campos son de sólo lectura y no viajan en el create,
        # así que todo tiene que salir del default_get con el contexto de la acción.
        return self.env["yaguven.volume.discount.wizard"].with_context(action["context"]).create({})

    def _bonus_lines(self, move):
        return move.invoice_line_ids.filtered("vd_source_move_id")

    # ------------------------------------------------------------------- casos

    def test_below_threshold_posts_normally(self):
        move = self._invoice(5, 1_000_000, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertFalse(self._bonus_lines(move))

    def test_without_scale_posts_normally(self):
        self.company.vd_tier_ids.unlink()
        move = self._invoice(5, 9_000_000, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_cross_first_tier(self):
        inv1 = self._invoice(5, 1_000_000)
        inv2 = self._invoice(15, 2_000_000)
        inv3 = self._invoice(27, 3_000_000, post=False)

        wizard = self._wizard(inv3)
        self.assertEqual(wizard.total, 6_000_000)
        self.assertEqual(wizard.percent, 4)
        result = wizard.action_apply()

        self.assertEqual(inv3.state, "posted")
        self.assertAlmostEqual(self._bonus_lines(inv3).price_subtotal, -120_000)
        self.assertEqual(inv3.amount_untaxed, 2_880_000)

        refund = self.env["account.move"].browse(result["res_id"])
        self.assertEqual(refund.move_type, "out_refund")
        self.assertEqual(refund.state, "draft")
        by_source = {l.vd_source_move_id: l.price_subtotal for l in refund.invoice_line_ids}
        self.assertEqual(by_source, {inv1: 40_000, inv2: 80_000})

        # La NC en borrador ya cuenta como aplicada: la factura siguiente del mes sólo
        # se bonifica a sí misma.
        inv4 = self._invoice(29, 1_000_000, post=False)
        wizard = self._wizard(inv4)
        pending = wizard.line_ids.filtered("pending_percent")
        self.assertEqual(pending.move_id, inv4)

    def test_next_tier_only_difference(self):
        inv1 = self._invoice(5, 1_000_000)
        inv2 = self._invoice(15, 2_000_000)
        inv3 = self._invoice(27, 3_000_000, post=False)
        self._wizard(inv3).action_apply()

        inv4 = self._invoice(28, 5_000_000, post=False)  # acumulado 11.000.000 -> 5%
        wizard = self._wizard(inv4)
        self.assertEqual(wizard.percent, 5)
        result = wizard.action_apply()

        self.assertAlmostEqual(self._bonus_lines(inv4).price_subtotal, -250_000)
        refund = self.env["account.move"].browse(result["res_id"])
        by_source = {l.vd_source_move_id: (l.vd_percent, l.price_subtotal) for l in refund.invoice_line_ids}
        self.assertEqual(by_source, {
            inv1: (1, 10_000), inv2: (1, 20_000), inv3: (1, 30_000),
        })

    def test_partner_scale_replaces_general(self):
        self.env["yaguven.volume.discount.tier"].create({
            "company_id": self.company.id, "partner_id": self.partner_a.id,
            "amount_from": 1_000_000, "percent": 8,
        })
        move = self._invoice(5, 1_500_000, post=False)
        wizard = self._wizard(move)
        self.assertEqual(wizard.percent, 8)
        # partner_b sigue con la escala general: 1.500.000 no llega a ningún tramo.
        other = self._invoice(5, 1_500_000, partner=self.partner_b, post=False)
        other.action_post()
        self.assertEqual(other.state, "posted")

    def test_post_without_discount_leaves_trace(self):
        self._invoice(5, 4_000_000)
        move = self._invoice(6, 2_000_000, post=False)
        self._wizard(move).action_post_without_discount()
        self.assertEqual(move.state, "posted")
        self.assertFalse(self._bonus_lines(move))
        self.assertIn("sin bonificación", move.message_ids[:1].body)

    def test_only_paid_ignores_unpaid(self):
        self.company.vd_only_paid = True
        self._invoice(5, 4_000_000)  # sin cobrar: no suma
        move = self._invoice(6, 2_000_000, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_refund_reduces_volume(self):
        inv = self._invoice(5, 4_000_000)
        refund = inv._reverse_moves([{"invoice_date": "2026-11-06"}])
        refund.action_post()
        move = self._invoice(7, 2_000_000, post=False)  # 4M - 4M + 2M = 2M
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_mass_post_is_blocked(self):
        self._invoice(5, 4_000_000)
        move = self._invoice(6, 2_000_000, post=False)
        with self.assertRaises(UserError):
            move._post()

    def test_missing_config_warns(self):
        self.company.vd_base = False
        move = self._invoice(5, 1_000_000, post=False)
        with self.assertRaises(UserError):
            move.action_post()
