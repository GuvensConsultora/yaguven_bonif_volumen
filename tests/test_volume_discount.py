from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestVolumeDiscount(AccountTestInvoicingCommon):
    """Escala real de BAIML (02/11/2026): 4% desde 8.000.000 y 8% desde 33.000.000 en el mes.

    La escala vive en la lista de precios "Con bonificación", asignada a partner_a.
    partner_b tiene la lista "Sin bonificación". Facturas del 5, 15 y 27 por 2, 3 y 4
    millones: la del 27 lleva el acumulado a 9.000.000 y cruza el primer tramo.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        # Sin la opción "Listas de precios" activa, Odoo no resuelve la lista del cliente
        # (property_product_pricelist vuelve vacío). Se activa igual que desde Ajustes.
        cls.env.ref("base.group_user")._apply_group(cls.env.ref("product.group_product_pricelist"))
        cls.bonus_product = cls.env["product.product"].create({
            "name": "Bonificación por volumen", "type": "service", "taxes_id": [fields.Command.clear()],
        })
        cls.product_a.taxes_id = [fields.Command.clear()]
        cls.company.write({
            "vd_product_id": cls.bonus_product.id,
            "vd_base": "untaxed",
            "vd_only_paid": False,
        })
        cls.pricelist = cls.env["product.pricelist"].create({
            "name": "Con bonificación",
            "company_id": cls.company.id,
            "vd_enabled": True,
            "vd_tier_ids": [
                fields.Command.create({"amount_from": 8_000_000, "percent": 4}),
                fields.Command.create({"amount_from": 33_000_000, "percent": 8}),
            ],
        })
        cls.pricelist_plain = cls.env["product.pricelist"].create({
            "name": "Sin bonificación", "company_id": cls.company.id,
        })
        cls.partner_a.with_company(cls.company).property_product_pricelist = cls.pricelist
        cls.partner_b.with_company(cls.company).property_product_pricelist = cls.pricelist_plain

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

    # ---------------------------------------------------------- escala por lista

    def test_scale_comes_from_partner_pricelist(self):
        Tier = self.env["yaguven.volume.discount.tier"]
        self.assertEqual(Tier._get_scale(self.company, self.partner_a), self.pricelist.vd_tier_ids)
        self.assertFalse(Tier._get_scale(self.company, self.partner_b))

    def test_pricelist_disabled_posts_normally(self):
        self.pricelist.vd_enabled = False
        move = self._invoice(5, 9_000_000, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertFalse(self._bonus_lines(move))

    def test_pricelist_without_bonus_posts_normally(self):
        move = self._invoice(5, 9_000_000, partner=self.partner_b, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_without_tiers_posts_normally(self):
        self.pricelist.vd_tier_ids.unlink()
        move = self._invoice(5, 9_000_000, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_partner_scale_replaces_pricelist(self):
        self.env["yaguven.volume.discount.tier"].create({
            "company_id": self.company.id, "partner_id": self.partner_b.id,
            "amount_from": 1_000_000, "percent": 3,
        })
        move = self._invoice(5, 1_500_000, partner=self.partner_b, post=False)
        wizard = self._wizard(move)
        self.assertEqual(wizard.percent, 3)
        # partner_a sigue con la escala de su lista: 1.500.000 no llega a ningún tramo.
        other = self._invoice(5, 1_500_000, post=False)
        other.action_post()
        self.assertEqual(other.state, "posted")

    def test_tier_belongs_to_exactly_one_scale(self):
        Tier = self.env["yaguven.volume.discount.tier"]
        with self.assertRaises(ValidationError):
            Tier.create({"amount_from": 1_000_000, "percent": 2})
        with self.assertRaises(ValidationError):
            Tier.create({
                "pricelist_id": self.pricelist.id, "partner_id": self.partner_a.id,
                "amount_from": 1_000_000, "percent": 2,
            })

    # ------------------------------------------------------------------ cálculo

    def test_below_threshold_posts_normally(self):
        move = self._invoice(5, 2_000_000, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertFalse(self._bonus_lines(move))

    def test_cross_first_tier(self):
        inv1 = self._invoice(5, 2_000_000)
        inv2 = self._invoice(15, 3_000_000)
        inv3 = self._invoice(27, 4_000_000, post=False)

        wizard = self._wizard(inv3)
        self.assertEqual(wizard.total, 9_000_000)
        self.assertEqual(wizard.percent, 4)
        result = wizard.action_apply()

        self.assertEqual(inv3.state, "posted")
        self.assertAlmostEqual(self._bonus_lines(inv3).price_subtotal, -160_000)
        self.assertEqual(inv3.amount_untaxed, 3_840_000)

        refund = self.env["account.move"].browse(result["res_id"])
        self.assertEqual(refund.move_type, "out_refund")
        by_source = {l.vd_source_move_id: l.price_subtotal for l in refund.invoice_line_ids}
        self.assertEqual(by_source, {inv1: 80_000, inv2: 120_000})

        # La NC ya cuenta como aplicada: la factura siguiente del mes sólo se bonifica a sí misma.
        inv4 = self._invoice(29, 1_000_000, post=False)
        wizard = self._wizard(inv4)
        pending = wizard.line_ids.filtered("pending_percent")
        self.assertEqual(pending.move_id, inv4)

    def test_next_tier_only_difference(self):
        inv1 = self._invoice(5, 2_000_000)
        inv2 = self._invoice(15, 3_000_000)
        inv3 = self._invoice(27, 4_000_000, post=False)
        self._wizard(inv3).action_apply()

        inv4 = self._invoice(28, 25_000_000, post=False)  # acumulado 34.000.000 -> 8%
        wizard = self._wizard(inv4)
        self.assertEqual(wizard.percent, 8)
        result = wizard.action_apply()

        self.assertAlmostEqual(self._bonus_lines(inv4).price_subtotal, -2_000_000)
        refund = self.env["account.move"].browse(result["res_id"])
        by_source = {l.vd_source_move_id: (l.vd_percent, l.price_subtotal) for l in refund.invoice_line_ids}
        # Ya tenían el 4%: la NC bonifica el otro 4%.
        self.assertEqual(by_source, {
            inv1: (4, 80_000), inv2: (4, 120_000), inv3: (4, 160_000),
        })

    def test_post_without_discount_leaves_trace(self):
        self._invoice(5, 6_000_000)
        move = self._invoice(6, 3_000_000, post=False)
        self._wizard(move).action_post_without_discount()
        self.assertEqual(move.state, "posted")
        self.assertFalse(self._bonus_lines(move))
        self.assertIn("sin bonificación", move.message_ids[:1].body)

    def test_only_paid_ignores_unpaid(self):
        self.company.vd_only_paid = True
        self._invoice(5, 6_000_000)  # sin cobrar: no suma
        move = self._invoice(6, 3_000_000, post=False)
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_refund_reduces_volume(self):
        inv = self._invoice(5, 6_000_000)
        refund = inv._reverse_moves([{"invoice_date": "2026-11-06"}])
        refund.action_post()
        move = self._invoice(7, 3_000_000, post=False)  # 6M - 6M + 3M = 3M
        move.action_post()
        self.assertEqual(move.state, "posted")

    def test_mass_post_is_blocked(self):
        self._invoice(5, 6_000_000)
        move = self._invoice(6, 3_000_000, post=False)
        with self.assertRaises(UserError):
            move._post()

    def test_missing_config_warns(self):
        self.company.vd_base = False
        move = self._invoice(5, 1_000_000, post=False)
        with self.assertRaises(UserError):
            move.action_post()

    # ------------------------------------------------------- base: importe bruto

    def test_base_is_gross_before_line_discount(self):
        """Importe bruto = cantidad x precio, antes del descuento de la línea (criterio de
        BAIML, 02/11/2026). 10.000.000 con 30% de descuento suma 10.000.000, no 7.000.000."""
        move = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.partner_a.id,
            "invoice_date": "2026-11-05",
            "invoice_line_ids": [fields.Command.create({
                "product_id": self.product_a.id, "quantity": 1, "price_unit": 10_000_000,
                "discount": 30, "tax_ids": [fields.Command.clear()],
            })],
        })
        wizard = self._wizard(move)
        self.assertEqual(wizard.percent, 4)
        wizard.action_apply()
        # El 4% se calcula sobre el bruto: 400.000, no 280.000.
        self.assertAlmostEqual(self._bonus_lines(move).price_subtotal, -400_000)

    def test_excluded_product_does_not_count(self):
        """El flete no suma al acumulado ni recibe bonificación."""
        freight = self.env["product.product"].create({
            "name": "Flete y/o seguro", "type": "service", "taxes_id": [fields.Command.clear()],
        })
        self.company.vd_excluded_product_ids = freight
        lines = [
            fields.Command.create({"product_id": self.product_a.id, "quantity": 1,
                                   "price_unit": 7_900_000, "tax_ids": [fields.Command.clear()]}),
            fields.Command.create({"product_id": freight.id, "quantity": 1,
                                   "price_unit": 500_000, "tax_ids": [fields.Command.clear()]}),
        ]
        move = self.env["account.move"].create({
            "move_type": "out_invoice", "partner_id": self.partner_a.id,
            "invoice_date": "2026-11-05", "invoice_line_ids": lines,
        })
        # 7.900.000 + 500.000 de flete pasaría los 8.000.000; sin el flete no llega.
        move.action_post()
        self.assertEqual(move.state, "posted")
        self.assertFalse(self._bonus_lines(move))
        # Con otra factura cruza el tramo: el 4% va sobre la mercadería, nunca sobre el flete.
        inv2 = self._invoice(10, 200_000, post=False)
        wizard = self._wizard(inv2)
        wizard.action_apply()
        refund_lines = self.env["account.move.line"].search([("vd_source_move_id", "=", move.id)])
        self.assertAlmostEqual(sum(refund_lines.mapped("price_subtotal")), 316_000)
