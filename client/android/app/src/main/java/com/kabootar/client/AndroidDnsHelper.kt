package com.kabootar.client

import android.net.ConnectivityManager
import android.net.DnsResolver
import android.net.LinkProperties
import android.net.Network
import android.os.Build
import android.os.CancellationSignal
import android.util.Log
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference

object AndroidDnsHelper {
    private const val TAG = "KabootarAndroid"
    private val callbackExecutor = Executors.newCachedThreadPool { runnable ->
        Thread(runnable, "kabootar-dns").apply {
            isDaemon = true
        }
    }

    @JvmStatic
    fun rawQuerySystem(query: ByteArray, timeoutMs: Int): ByteArray {
        val appContext = KabootarApp.appContext()
        val connectivity = appContext.getSystemService(ConnectivityManager::class.java)
            ?: throw IllegalStateException("connectivity_manager_unavailable")
        val network = connectivity.activeNetwork
            ?: throw IOException("active_network_unavailable")
        val boundedTimeoutMs = timeoutMs.coerceIn(1000, 30000)

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            runCatching {
                return rawQueryWithDnsResolver(network, query, boundedTimeoutMs)
            }.onFailure { exc ->
                Log.w(TAG, "DnsResolver.rawQuery failed; falling back to active link DNS servers", exc)
            }
        }

        val linkProperties = connectivity.getLinkProperties(network)
            ?: throw IOException("link_properties_unavailable")
        val dnsServers = linkProperties.dnsServers
        if (dnsServers.isEmpty()) {
            throw IOException("link_dns_servers_unavailable")
        }
        return rawQueryViaLinkDns(network, linkProperties, dnsServers, query, boundedTimeoutMs)
    }

    private fun rawQueryViaLinkDns(
        network: Network,
        linkProperties: LinkProperties,
        dnsServers: List<InetAddress>,
        query: ByteArray,
        timeoutMs: Int,
    ): ByteArray {
        var lastError: Throwable? = null
        for (server in dnsServers) {
            try {
                val udpResponse = rawUdpQuery(network, server, query, timeoutMs)
                if (!isTruncated(udpResponse)) {
                    return udpResponse
                }
                return rawTcpQuery(network, server, query, timeoutMs)
            } catch (exc: Throwable) {
                lastError = exc
                Log.w(
                    TAG,
                    "Link DNS query failed via ${server.hostAddress} privateDns=${isPrivateDnsActive(linkProperties)}",
                    exc,
                )
            }
        }
        throw IOException("android_link_dns_failed:${lastError?.message ?: "unknown"}", lastError)
    }

    private fun rawUdpQuery(
        network: Network,
        server: InetAddress,
        query: ByteArray,
        timeoutMs: Int,
    ): ByteArray {
        val socket = DatagramSocket()
        try {
            network.bindSocket(socket)
            socket.soTimeout = timeoutMs
            socket.send(DatagramPacket(query, query.size, server, 53))
            val buffer = ByteArray(8192)
            val response = DatagramPacket(buffer, buffer.size)
            socket.receive(response)
            val packet = buffer.copyOf(response.length)
            if (!sameDnsTransactionId(query, packet)) {
                throw IOException("android_udp_dns_mismatched_transaction")
            }
            return packet
        } finally {
            socket.close()
        }
    }

    private fun rawTcpQuery(
        network: Network,
        server: InetAddress,
        query: ByteArray,
        timeoutMs: Int,
    ): ByteArray {
        val socket = network.socketFactory.createSocket(server, 53)
        socket.soTimeout = timeoutMs
        socket.tcpNoDelay = true
        try {
            val output = DataOutputStream(socket.getOutputStream())
            output.writeShort(query.size)
            output.write(query)
            output.flush()
            DataInputStream(socket.getInputStream()).use { input ->
                val responseLength = input.readUnsignedShort()
                if (responseLength <= 0) {
                    throw IOException("android_tcp_dns_empty")
                }
                val response = ByteArray(responseLength)
                input.readFully(response)
                if (!sameDnsTransactionId(query, response)) {
                    throw IOException("android_tcp_dns_mismatched_transaction")
                }
                return response
            }
        } finally {
            socket.close()
        }
    }

    @androidx.annotation.RequiresApi(Build.VERSION_CODES.Q)
    private fun rawQueryWithDnsResolver(
        network: Network,
        query: ByteArray,
        timeoutMs: Int,
    ): ByteArray {
        val latch = CountDownLatch(1)
        val answerRef = AtomicReference<ByteArray?>()
        val errorRef = AtomicReference<Throwable?>()
        val rcodeRef = AtomicReference(0)
        val cancellation = CancellationSignal()

        DnsResolver.getInstance().rawQuery(
            network,
            query,
            DnsResolver.FLAG_EMPTY,
            callbackExecutor,
            cancellation,
            object : DnsResolver.Callback<ByteArray> {
                override fun onAnswer(answer: ByteArray, rcode: Int) {
                    answerRef.set(answer)
                    rcodeRef.set(rcode)
                    latch.countDown()
                }

                override fun onError(error: DnsResolver.DnsException) {
                    errorRef.set(error)
                    latch.countDown()
                }
            },
        )

        if (!latch.await(timeoutMs.toLong(), TimeUnit.MILLISECONDS)) {
            cancellation.cancel()
            throw SocketTimeoutException("android_dns_resolver_timeout")
        }

        val error = errorRef.get()
        if (error != null) {
            throw IOException("android_dns_resolver_error:${error.message ?: error.javaClass.simpleName}", error)
        }

        val answer = answerRef.get()
            ?: throw IOException("android_dns_resolver_empty")
        val rcode = rcodeRef.get()
        if (rcode != 0 && rcode != 3) {
            throw IOException("android_dns_resolver_rcode=$rcode")
        }
        return answer
    }

    private fun sameDnsTransactionId(query: ByteArray, response: ByteArray): Boolean {
        if (query.size < 2 || response.size < 2) {
            return false
        }
        return query[0] == response[0] && query[1] == response[1]
    }

    private fun isTruncated(packet: ByteArray): Boolean {
        if (packet.size < 4) {
            return false
        }
        return packet[2].toInt() and 0x02 != 0
    }

    private fun isPrivateDnsActive(linkProperties: LinkProperties): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.P) {
            return false
        }
        return linkProperties.isPrivateDnsActive
    }
}
