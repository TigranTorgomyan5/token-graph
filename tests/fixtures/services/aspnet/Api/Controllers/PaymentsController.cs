using Microsoft.AspNetCore.Mvc;

namespace Api.Controllers;

[ApiController]
[Route("api/v1/[controller]")]
public class PaymentsController : ControllerBase
{
    private readonly Dictionary<string, int> _byRoute = new();

    [HttpPost("charge")]
    public async Task<IActionResult> Charge(ChargeRequest request)
    {
        var count = _byRoute[route];
        return Ok();
    }

    [HttpGet("{id}")]
    public IActionResult Get(string id) => Ok();

    [HttpGet("/status")]
    public IActionResult Status() => Ok();
}

public record ChargeRequest(string Card, decimal Amount);
