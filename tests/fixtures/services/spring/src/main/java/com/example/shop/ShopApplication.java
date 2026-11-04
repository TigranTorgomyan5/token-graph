package com.example.shop;

import org.springframework.boot.SpringApplication;

public class ShopApplication {
    public static void main(String[] args) {
        Runtime.getRuntime().addShutdownHook(new Thread(() -> {}));
        SpringApplication.run(ShopApplication.class, args);
    }
}
