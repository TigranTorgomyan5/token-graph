const router = require('express').Router();
const controller = require('../controllers/cart');

router.post('/add', validate, controller.addItem);
router.route('/items').get(controller.list).delete(controller.clear);
// router.get('/commented-out', controller.old);

module.exports = router;
