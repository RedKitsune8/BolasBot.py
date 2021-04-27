# pylint: disable=import-error

import re
import time

import discord

from discord.ext import commands

from src.db_utils import (open_db, commit_db, close_db, insert_player_if_not_exists,
    insert_async, get_async_by_submit, get_active_async_races, update_async_status, save_async_result,
    get_results_for_race, get_player_by_id, get_async_history_channel, set_async_history_channel) 

from src.seedgen import generate_from_preset, generate_from_hash, generate_from_yaml, generate_from_attachment, is_preset


def get_results_text(db_cur, submit_channel):
    results = get_results_for_race(db_cur, submit_channel)
    msg = "```\n"
    msg += "+" + "-"*47 + "+\n"
    msg += "| Pos. | Jugador              | Tiempo   | Col. |\n"
    
    pos = 1
    for res in results:
        m, s = divmod(res[1], 60)
        h, m = divmod(m, 60)
        time_str = "{:02d}:{:02d}:{:02d}".format(h, m, s)
        msg += "|" + "-" * 47 + "|\n"
        msg += "| {:4d} | {:20s} | {} | {:4d} |\n".format(pos, res[0], time_str, res[2])
        pos += 1
    
    msg += "+" + "-"*47 + "+\n"
    msg += "```"
    return msg


def get_async_data(db_cur, submit_channel):
    my_async = get_async_by_submit(db_cur, submit_channel)
    player = get_player_by_id(db_cur, my_async[2])

    msg = "__**CARRERA ASÍNCRONA: {}**__\n".format(my_async[1])
    msg += "**Iniciada por: **{}\n".format(player[1])
    msg += "**Fecha de inicio (UTC): **{}\n".format(my_async[3])
    if my_async[4]:
        msg += "**Fecha de cierre (UTC): **{}\n".format(my_async[4])
    if my_async[6]:
        msg += "**Descripción: **{}\n".format(my_async[6])
    if my_async[9]:
        msg += "**Seed: **{}".format(my_async[9])
    if my_async[8]:
        msg += " ({})".format(my_async[8])
    
    return msg


def check_race_permissions(ctx, race):
    auth_permissions = ctx.author.permissions_in(ctx.guild.get_channel(race[11]))
    if auth_permissions.manage_channels or ctx.author.id == race[2]:
        return True
    
    return False


    ########################################


class AsyncRace(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    
    @commands.command()
    @commands.guild_only()
    async def asyncstart(self, ctx, name: str, *preset):
        db_conn, db_cur = open_db(ctx.guild.id)
        
        creator = ctx.author
        insert_player_if_not_exists(db_cur, creator.id, creator.name, creator.discriminator, creator.mention)

        # Comprobación de límite: máximo de 10 asíncronas en el servidor
        asyncs = get_active_async_races(db_cur)
        if asyncs and len(asyncs) >= 10:
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("Demasiadas asíncronas activas en el servidor. Contacta a un moderador para purgar alguna.")

        # Comprobación de nombre válido
        if re.match(r'https://alttpr\.com/h/\w{10}$', name) or is_preset(name):
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("El nombre de la carrera no puede ser un preset o una URL de seed.")
        
        if len(name) > 20:
            name = name[:20]

        # Crear o procesar seed
        seed = None
        seed_hash = None
        seed_code = None
        seed_url = None
        desc = " ".join(preset)

        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
            try:
                seed = await generate_from_attachment(attachment)
            except:
                close_db(db_conn)
                raise commands.errors.CommandInvokeError("Error al generar la seed. Asegúrate de que el YAML introducido sea válido.")

        elif preset:
            if re.match(r'https://alttpr\.com/h/\w{10}$', preset[0]):
                seed = await generate_from_hash((preset[0]).split('/')[-1])
                if seed:
                    desc = " ".join(preset[1:])
            else:
                seed = await generate_from_preset(preset)

        if seed:
            seed_hash = seed.hash
            seed_url = seed.url
            seed_code = " | ".join(seed.code)

        # Crear canales y rol para la async

        server = ctx.guild

        async_role = await server.create_role(name=name)
        res_overwrites = {
            server.default_role: discord.PermissionOverwrite(read_messages=False, send_messages=False),
            server.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            async_role: discord.PermissionOverwrite(read_messages=True)
        }
        spoiler_overwrites = {
            server.default_role: discord.PermissionOverwrite(read_messages=False),
            server.me: discord.PermissionOverwrite(read_messages=True),
            async_role: discord.PermissionOverwrite(read_messages=True)
        }

        async_category = await server.create_category_channel(name)
        submit_channel = await server.create_text_channel("{}-submit".format(name), category=async_category)
        results_channel = await server.create_text_channel("{}-results".format(name), category=async_category, overwrites=res_overwrites)
        spoilers_channel = await server.create_text_channel("{}-spoilers".format(name), category=async_category, overwrites=spoiler_overwrites)

        results_text = get_results_text(db_cur, submit_channel.id)
        results_msg = await results_channel.send(results_text)
               
        insert_async(db_cur, name, creator.id, desc, seed_hash, seed_code, seed_url, async_role.id,
                     submit_channel.id, results_channel.id, results_msg.id, spoilers_channel.id)

        commit_db(db_conn)
        async_data = get_async_data(db_cur, submit_channel.id)
        close_db(db_conn)

        data_msg = await submit_channel.send(async_data)
        await data_msg.pin()
        await submit_channel.send("Por favor, mantened este canal lo más limpio posible y SIN SPOILERS.")

        text_ans = 'Abierta carrera asíncrona con nombre: {}\nEnvía resultados en {}'.format(name, submit_channel.mention)

        await ctx.reply(text_ans, mention_author=False)


    @asyncstart.error
    async def asyncstart_error(self, ctx, error):
        error_mes = "Se ha producido un error."
        if type(error) == commands.errors.MissingRequiredArgument:
            error_mes = "Faltan argumentos para ejecutar el comando."
        elif type(error) == commands.errors.BadArgument:
            error_mes = "Argumentos inválidos."
        elif type(error) == commands.errors.CommandInvokeError:
            error_mes = error.original
        
        err_file = discord.File("res/error.png")
        await ctx.reply(error_mes, mention_author=False, file=err_file)  


    ########################################


    @commands.command()
    @commands.guild_only()
    async def end(self, ctx):
        db_conn, db_cur = open_db(ctx.guild.id)

        author = ctx.author
        insert_player_if_not_exists(db_cur, author.id, author.name, author.discriminator, author.mention)

        race = get_async_by_submit(db_cur, ctx.channel.id)

        if not race:
            close_db(db_conn)
            return

        if not check_race_permissions(ctx, race):
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("Esta operación solo puede realizarla el creador original de la carrera o un moderador.")

        if race[5] == 0:
            update_async_status(db_cur, race[0], 1)
            commit_db(db_conn)
            close_db(db_conn)
            await ctx.reply("Esta carrera ha sido cerrada.", mention_author=False)
        else:
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("Esta carrera no está abierta.")

    
    @end.error
    async def end_error(self, ctx, error):
        error_mes = "Se ha producido un error."
        if type(error) == commands.errors.MissingRequiredArgument:
            error_mes = "Faltan argumentos para ejecutar el comando."
        elif type(error) == commands.errors.BadArgument:
            error_mes = "Argumentos inválidos."
        elif type(error) == commands.errors.CommandInvokeError:
            error_mes = error.original
        
        err_file = discord.File("res/error.png")
        await ctx.reply(error_mes, mention_author=False, file=err_file)  


    ########################################


    @commands.command()
    @commands.guild_only()
    async def reopen(self, ctx):
        db_conn, db_cur = open_db(ctx.guild.id)

        author = ctx.author
        insert_player_if_not_exists(db_cur, author.id, author.name, author.discriminator, author.mention)

        race = get_async_by_submit(db_cur, ctx.channel.id)

        if not race:
            close_db(db_conn)
            return

        if not check_race_permissions(ctx, race):
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("Esta operación solo puede realizarla el creador original de la carrera o un moderador.")

        if race[5] == 1:
            update_async_status(db_cur, race[0], 0)
            commit_db(db_conn)
            close_db(db_conn)
            await ctx.reply("Esta carrera ha sido reabierta.", mention_author=False)
        else:
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("Esta carrera no está cerrada.")

    
    @reopen.error
    async def reopen_error(self, ctx, error):
        error_mes = "Se ha producido un error."
        if type(error) == commands.errors.MissingRequiredArgument:
            error_mes = "Faltan argumentos para ejecutar el comando."
        elif type(error) == commands.errors.BadArgument:
            error_mes = "Argumentos inválidos."
        elif type(error) == commands.errors.CommandInvokeError:
            error_mes = error.original
        
        err_file = discord.File("res/error.png")
        await ctx.reply(error_mes, mention_author=False, file=err_file)  


    ########################################


    @commands.command()
    @commands.guild_only()
    async def purge(self, ctx):
        db_conn, db_cur = open_db(ctx.guild.id)

        author = ctx.author
        insert_player_if_not_exists(db_cur, author.id, author.name, author.discriminator, author.mention)

        race = get_async_by_submit(db_cur, ctx.channel.id)

        if not race:
            close_db(db_conn)
            return

        if not check_race_permissions(ctx, race):
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("Esta operación solo puede realizarla el creador original de la carrera o un moderador.")

        if race[5] == 1:
            update_async_status(db_cur, race[0], 2)

            # Copia de resultados al historial, si los hay
            submit_channel = ctx.guild.get_channel(race[11])
            results = get_results_for_race(db_cur, submit_channel.id)
            if results:
                history_channel = get_async_history_channel(db_cur)
                my_hist_channel = None
                if not history_channel[0] or not ctx.guild.get_channel(history_channel[0]):
                    history_overwrites = {
                        ctx.guild.default_role: discord.PermissionOverwrite(send_messages=False),
                        ctx.guild.me: discord.PermissionOverwrite(send_messages=True)
                    }
                    my_hist_channel = await ctx.guild.create_text_channel("async-historico", overwrites=history_overwrites)
                    set_async_history_channel(db_cur, my_hist_channel.id)
                else:
                    my_hist_channel = ctx.guild.get_channel(history_channel[0])

                hist_msg = get_async_data(db_cur, submit_channel.id) + "\n" + get_results_text(db_cur, submit_channel.id)
                await my_hist_channel.send(hist_msg)

            # Eliminación de roles y canales            

            async_role = ctx.guild.get_role(race[10])
            await async_role.delete()

            category = submit_channel.category
            await submit_channel.delete()

            results_channel = ctx.guild.get_channel(race[12])
            await results_channel.delete()

            spoilers_channel = ctx.guild.get_channel(race[14])
            await spoilers_channel.delete()

            await category.delete()

            commit_db(db_conn)
            close_db(db_conn)
        
        else:
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("La carrera debe cerrarse antes de ser purgada.")


    @purge.error
    async def purge_error(self, ctx, error):
        error_mes = "Se ha producido un error."
        if type(error) == commands.errors.MissingRequiredArgument:
            error_mes = "Faltan argumentos para ejecutar el comando."
        elif type(error) == commands.errors.BadArgument:
            error_mes = "Argumentos inválidos."
        elif type(error) == commands.errors.CommandInvokeError:
            error_mes = error.original
        
        err_file = discord.File("res/error.png")
        await ctx.reply(error_mes, mention_author=False, file=err_file) 


    ########################################


    @commands.command()
    @commands.guild_only()
    async def done(self, ctx, time: str, collection: int=0):
        message = ctx.message
        await message.delete()

        db_conn, db_cur = open_db(ctx.guild.id)

        author = ctx.author
        insert_player_if_not_exists(db_cur, author.id, author.name, author.discriminator, author.mention)

        race = get_async_by_submit(db_cur, ctx.channel.id)

        if not race:
            close_db(db_conn)
            return

        if race[5] == 0:
            if re.match(r'\d?\d:[0-5]\d:[0-5]\d$', time):
                time_arr = [int(x) for x in time.split(':')]
                time_s = 3600*time_arr[0] + 60*time_arr[1] + time_arr[2]
                save_async_result(db_cur, race[0], author.id, time_s, collection)

                results_text = get_results_text(db_cur, race[11])
                results_channel = ctx.guild.get_channel(race[12])
                results_msg = await results_channel.fetch_message(race[13])
                await results_msg.edit(content=results_text)

                await ctx.send("GG {}, tu resultado se ha registrado.".format(author.mention))
                async_role = ctx.guild.get_role(race[10])
                await author.add_roles(async_role)
        
            else:
                close_db(db_conn)
                raise commands.errors.CommandInvokeError("Tiempo inválido.")
        
        else:
            close_db(db_conn)
            raise commands.errors.CommandInvokeError("Esta carrera asíncrona no está abierta.")
        
        commit_db(db_conn)
        close_db(db_conn)


    @done.error
    async def done_error(self, ctx, error):
        error_mes = "Se ha producido un error."
        if type(error) == commands.errors.MissingRequiredArgument:
            message = ctx.message
            await message.delete()
            error_mes = "Faltan argumentos para ejecutar el comando."
        elif type(error) == commands.errors.BadArgument:
            message = ctx.message
            await message.delete()
            error_mes = "Argumentos inválidos."
        elif type(error) == commands.errors.CommandInvokeError:
            error_mes = error.original
        
        err_file = discord.File("res/error.png")
        await ctx.send(error_mes, file=err_file)
        