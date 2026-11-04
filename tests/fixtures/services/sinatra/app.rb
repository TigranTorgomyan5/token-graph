require 'sinatra'

# get '/commented' do
get '/health' do
  'ok'
end

post '/signup' do
  create_user
end
