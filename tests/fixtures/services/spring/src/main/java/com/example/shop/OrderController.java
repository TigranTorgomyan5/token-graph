package com.example.shop;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @PostMapping("/place")
    public Order place(@RequestBody Order order) {
        return service.place(order);
    }

    @GetMapping
    public List<Order> list() {
        return service.all();
    }

    @RequestMapping(value = "/{id}/cancel", method = RequestMethod.POST)
    public void cancel(@PathVariable String id) {
    }
}
