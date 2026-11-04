// Payments API host.
var builder = WebApplication.CreateBuilder(args);
builder.Services.AddControllers();
var app = builder.Build();

app.MapControllers();
var api = app.MapGroup("/api");
var refunds = api.MapGroup("/refunds");
refunds.MapPost("/create", CreateRefund);
app.MapGet("/health", () => "ok");
app.Lifetime.ApplicationStopping.Register(() => Console.WriteLine("stopping"));

app.Run();
